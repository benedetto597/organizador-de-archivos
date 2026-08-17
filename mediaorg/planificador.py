"""Construccion del plan de movimientos.

Es la capa de *politica*: decide en que carpeta cae cada archivo, cual copia
sobrevive y cual se aisla en `BORRAR`. Es codigo puro: no toca el disco mas
alla de comprobar si una ruta destino ya esta ocupada, de modo que el mismo
plan que se muestra en simulacion es exactamente el que se ejecuta.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .config import (
    CARPETA_BORRAR,
    CARPETA_SIN_FECHA,
    Configuracion,
    Jerarquia,
    JerarquiaArchivos,
)
from .duplicados import GrupoDuplicados
from .escaneo import ArchivoEncontrado
from .fechas import etiqueta_anio, etiqueta_mes
from .metadatos import Analisis
from .rutas_seguras import es_subruta, nombre_disponible, sanear_componente
from .tipos import TipoMedia

__all__ = ["Movimiento", "Plan", "construir_plan", "MOTIVO_ORGANIZAR"]

_log = logging.getLogger(__name__)

#: Motivo de un movimiento ordinario (no duplicado).
MOTIVO_ORGANIZAR = "organizado"


@dataclass(frozen=True, slots=True)
class Movimiento:
    """Una reubicacion concreta: de donde, a donde y por que."""

    origen: Path
    destino: Path
    tipo: str
    fuente: str
    fecha_iso: str | None
    origen_fecha: str
    motivo: str
    tamano: int
    #: Vacios en fotos y videos; poblados en el resto de archivos.
    categoria: str = ""
    subtipo: str = ""

    @property
    def es_duplicado(self) -> bool:
        """True si el archivo va a la cuarentena `BORRAR`."""
        return self.motivo != MOTIVO_ORGANIZAR

    @property
    def es_media(self) -> bool:
        """True si es foto, video o animacion (no tiene categoria de archivo)."""
        return not self.categoria


@dataclass(slots=True)
class Plan:
    """Conjunto completo de movimientos calculados para una corrida."""

    movimientos: list[Movimiento] = field(default_factory=list)
    ya_ordenados: int = 0
    descartados: int = 0
    duplicados: int = 0
    bytes_a_mover: int = 0
    bytes_duplicados: int = 0
    advertencias: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Cantidad de movimientos planificados."""
        return len(self.movimientos)


def construir_plan(
    items: Sequence[ArchivoEncontrado],
    analisis: Mapping[Path, Analisis],
    grupos: Sequence[GrupoDuplicados],
    cfg: Configuracion,
) -> Plan:
    """Calcula el destino de cada archivo.

    Args:
        items: Archivos aceptados por el escaneo.
        analisis: Metadatos por ruta.
        grupos: Grupos de duplicados detectados.
        cfg: Configuracion de la corrida.

    Returns:
        Un `Plan` con un movimiento por archivo que deba reubicarse. Los
        archivos que ya estan en su destino correcto no generan movimiento.
    """
    plan = Plan()
    duplicados = _marcar_duplicados(grupos, analisis)
    reservados: set[Path] = set()

    for item in items:
        info = analisis.get(item.ruta)
        if info is None:  # pragma: no cover - defensivo
            plan.descartados += 1
            plan.advertencias.append(f"Sin metadatos: {item.ruta}")
            continue

        motivo = duplicados.get(item.ruta, MOTIVO_ORGANIZAR)
        carpeta = _carpeta_destino(cfg, info, motivo)
        destino_ideal = carpeta / item.ruta.name

        if destino_ideal == item.ruta:
            reservados.add(destino_ideal)
            plan.ya_ordenados += 1
            continue

        if not es_subruta(destino_ideal, cfg.destino_efectivo):
            plan.descartados += 1
            plan.advertencias.append(f"Destino fuera del arbol permitido: {destino_ideal}")
            continue

        try:
            destino = nombre_disponible(destino_ideal, reservados)
        except Exception as exc:  # noqa: BLE001 - se reporta y se continua
            plan.descartados += 1
            plan.advertencias.append(f"{item.ruta}: {exc}")
            continue

        reservados.add(destino)
        movimiento = Movimiento(
            origen=item.ruta,
            destino=destino,
            tipo=info.tipo_efectivo,
            fuente=info.fuente,
            fecha_iso=info.fecha_iso,
            origen_fecha=info.origen_fecha,
            motivo=motivo,
            tamano=item.tamano,
            categoria=info.categoria,
            subtipo=info.subtipo,
        )
        plan.movimientos.append(movimiento)
        plan.bytes_a_mover += item.tamano
        if movimiento.es_duplicado:
            plan.duplicados += 1
            plan.bytes_duplicados += item.tamano

    return plan


# --------------------------------------------------------------------------- #
# Politica de duplicados
# --------------------------------------------------------------------------- #

def _clave_superviviente(item: ArchivoEncontrado, info: Analisis | None) -> tuple:
    """Clave de orden: el minimo es la copia que se conserva.

    Criterios, en orden:

    1. Fecha confiable (EXIF/contenedor/nombre) antes que fecha del sistema:
       la copia con metadatos intactos es la mas valiosa.
    2. Mayor tamano: normalmente conserva EXIF y no fue recomprimida.
    3. Fecha de modificacion mas antigua: suele ser el archivo original.
    4. Nombre mas corto y luego orden alfabetico: desempate determinista, de
       modo que dos corridas sobre el mismo acervo dan el mismo resultado.
    """
    confiable = 0 if (info is not None and info.tiene_fecha_confiable) else 1
    return (confiable, -item.tamano, item.mtime, len(item.ruta.name), str(item.ruta))


def _marcar_duplicados(
    grupos: Sequence[GrupoDuplicados], analisis: Mapping[Path, Analisis]
) -> dict[Path, str]:
    """Devuelve `{ruta: motivo}` para las copias que van a `BORRAR`.

    El superviviente de cada grupo no aparece en el diccionario: se organiza
    normalmente.
    """
    marcados: dict[Path, str] = {}
    for grupo in grupos:
        ordenados = sorted(
            grupo.integrantes,
            key=lambda item: _clave_superviviente(item, analisis.get(item.ruta)),
        )
        for copia in ordenados[1:]:
            marcados[copia.ruta] = grupo.motivo.value
    return marcados


# --------------------------------------------------------------------------- #
# Construccion de rutas
# --------------------------------------------------------------------------- #

def _segmentos_fecha(cfg: Configuracion, info: Analisis) -> tuple[str, ...]:
    """`('2024', '2024-05')`, o `('Sin-Fecha',)` cuando no hay fecha."""
    momento = info.fecha
    if momento is None:
        return (CARPETA_SIN_FECHA,)
    return (etiqueta_anio(momento), etiqueta_mes(momento, cfg.mes_con_nombre))


def _carpeta_destino(cfg: Configuracion, info: Analisis, motivo: str) -> Path:
    """Arma la carpeta destino segun la jerarquia configurada."""
    raiz_logica = _raiz_logica(info)
    if motivo != MOTIVO_ORGANIZAR:
        # Cuarentena: se agrupa por motivo y por la carpeta de primer nivel que
        # le habria correspondido, para poder revisarla rapido.
        segmentos: tuple[str, ...] = (CARPETA_BORRAR, motivo, raiz_logica)
    elif info.es_media:
        segmentos = _segmentos_media(cfg, info, raiz_logica)
    else:
        segmentos = _segmentos_archivo(cfg, info, raiz_logica)

    destino = cfg.destino_efectivo
    for segmento in segmentos:
        destino = destino / sanear_componente(segmento)
    return destino


def _raiz_logica(info: Analisis) -> str:
    """Carpeta de primer nivel: tipo audiovisual o categoria de archivo."""
    if info.es_media:
        return _tipo_desde_valor(info.tipo_efectivo).carpeta
    return info.categoria


def _segmentos_media(cfg: Configuracion, info: Analisis, raiz: str) -> tuple[str, ...]:
    """Arbol de fotos, videos y animaciones."""
    fecha = _segmentos_fecha(cfg, info)
    fuente = info.fuente
    if cfg.jerarquia is Jerarquia.TIPO_FECHA_FUENTE:
        return (raiz, *fecha, fuente)
    if cfg.jerarquia is Jerarquia.TIPO_FECHA:
        return (raiz, *fecha)
    if cfg.jerarquia is Jerarquia.TIPO_FUENTE:
        return (raiz, fuente)
    if cfg.jerarquia is Jerarquia.FECHA:
        return fecha
    return (raiz,)


def _segmentos_archivo(cfg: Configuracion, info: Analisis, raiz: str) -> tuple[str, ...]:
    """Arbol de documentos, comprimidos, instaladores y demas.

    Se usa solo el anio, sin mes: en un documento la granularidad mensual no
    aporta y multiplica carpetas casi vacias.
    """
    momento = info.fecha
    anio = etiqueta_anio(momento) if momento is not None else CARPETA_SIN_FECHA
    subtipo = info.subtipo or "Otros"
    if cfg.jerarquia_archivos is JerarquiaArchivos.CATEGORIA_SUBTIPO_ANIO:
        return (raiz, subtipo, anio)
    if cfg.jerarquia_archivos is JerarquiaArchivos.CATEGORIA_ANIO:
        return (raiz, anio)
    return (raiz,)


def _tipo_desde_valor(valor: str) -> TipoMedia:
    """Convierte el valor serializado de vuelta al enum."""
    try:
        return TipoMedia(valor)
    except ValueError:  # pragma: no cover - defensivo
        return TipoMedia.DESCONOCIDO
