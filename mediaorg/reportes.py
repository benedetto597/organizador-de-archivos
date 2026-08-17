"""Reportes de la corrida: CSV detallado, JSON de resumen y tabla de consola.

El CSV se escribe siempre, incluso en simulacion: es la forma de auditar el
plan completo antes de autorizar el movimiento de decenas de miles de
archivos.
"""

from __future__ import annotations

import csv
import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import Configuracion
from .duplicados import ResumenDuplicados
from .ejecutor import ResumenEjecucion
from .errores import ConfiguracionInvalida
from .escaneo import ResumenEscaneo
from .planificador import MOTIVO_ORGANIZAR, Movimiento, Plan
from .registro import formatear_bytes, formatear_duracion
from .rutas_seguras import asegurar_directorio, es_subruta, normalizar, ruta_del_sistema

__all__ = [
    "Informe",
    "escribir_csv",
    "escribir_json",
    "leer_csv",
    "texto_resumen",
    "marca_tiempo",
]

_log = logging.getLogger(__name__)

_CAMPOS_CSV = (
    "origen",
    "destino",
    "carpeta",
    "tipo",
    "categoria",
    "subtipo",
    "fuente",
    "fecha",
    "origen_fecha",
    "motivo",
    "tamano_bytes",
)

#: Columnas sin las que un plan no se puede aplicar. El resto son informativas,
#: asi que un CSV de una version anterior sigue siendo utilizable.
_CAMPOS_REQUERIDOS = frozenset({"origen", "destino", "motivo"})


#: Caracteres con los que una hoja de calculo interpreta la celda como formula.
#: Un archivo llamado `=cmd|'/c calc'!A1.jpg` se ejecutaria al abrir el CSV en
#: Excel, y el flujo documentado pide justamente abrirlo para revisar el plan.
_INICIOS_PELIGROSOS = frozenset("=+-@\t\r")


def marca_tiempo(momento: datetime | None = None) -> str:
    """`20260813-153045`, apto para nombres de archivo."""
    return (momento or datetime.now()).strftime("%Y%m%d-%H%M%S")


def _neutralizar(valor: str) -> str:
    """Antepone un apostrofo a las celdas que una hoja de calculo evaluaria.

    Examples:
        >>> _neutralizar('=cmd|calc!A1.jpg')
        "'=cmd|calc!A1.jpg"
        >>> _neutralizar('foto.jpg')
        'foto.jpg'
    """
    if valor and valor[0] in _INICIOS_PELIGROSOS:
        return f"'{valor}"
    return valor


def _limpiar(valor: str | None) -> str:
    """Inversa exacta de `_neutralizar`, con recorte de espacios.

    Solo quita el apostrofo cuando protege un caracter peligroso, de modo que
    un nombre que de verdad empieza con apostrofo sobrevive el viaje.

    Examples:
        >>> _limpiar("  '=formula.jpg ")
        '=formula.jpg'
        >>> _limpiar("'comillas.jpg")
        "'comillas.jpg"
    """
    texto = (valor or "").strip()
    if len(texto) >= 2 and texto[0] == "'" and texto[1] in _INICIOS_PELIGROSOS:
        return texto[1:]
    return texto


@dataclass(slots=True)
class Informe:
    """Todo lo que se sabe de una corrida, listo para serializar."""

    cfg: Configuracion
    escaneo: ResumenEscaneo
    duplicados: ResumenDuplicados
    plan: Plan
    ejecucion: ResumenEjecucion
    segundos: float
    csv: Path | None = None
    manifiesto: Path | None = None

    def conteo(self, atributo: str) -> dict[str, int]:
        """Cuenta movimientos por un atributo del movimiento."""
        contador: Counter[str] = Counter()
        for movimiento in self.plan.movimientos:
            contador[str(getattr(movimiento, atributo))] += 1
        return dict(contador.most_common())

    def conteo_anios(self) -> dict[str, int]:
        """Cuenta movimientos ordinarios por anio de la fecha resuelta."""
        contador: Counter[str] = Counter()
        for movimiento in self.plan.movimientos:
            if movimiento.es_duplicado:
                continue
            contador[movimiento.fecha_iso[:4] if movimiento.fecha_iso else "Sin-Fecha"] += 1
        return dict(sorted(contador.items()))

    def a_dict(self) -> dict:
        """Estructura plana y estable para el JSON de resumen."""
        return {
            "generado": datetime.now().isoformat(timespec="seconds"),
            "duracion_segundos": round(self.segundos, 2),
            "configuracion": {
                "raiz": str(self.cfg.raiz),
                "destino": str(self.cfg.destino_efectivo),
                "operacion": self.cfg.operacion.value,
                "modo": self.cfg.modo.value,
                "profundidad": self.cfg.profundidad,
                "jerarquia": self.cfg.jerarquia.value,
                "jerarquia_archivos": self.cfg.jerarquia_archivos.value,
                "duplicados": self.cfg.duplicados.value,
                "simulacion": self.cfg.simulacion,
                "procesos": self.cfg.procesos,
                "prefiltro_rapido": self.cfg.prefiltro_rapido,
            },
            "escaneo": {
                "carpetas": self.escaneo.carpetas,
                "archivos_vistos": self.escaneo.archivos,
                "aceptados": self.escaneo.aceptados,
                "aceptados_media": self.escaneo.aceptados_media,
                "aceptados_archivos": self.escaneo.aceptados_archivos,
                "ignorados_extension": self.escaneo.ignorados_extension,
                "ignorados_tamano": self.escaneo.ignorados_tamano,
                "enlaces_omitidos": self.escaneo.enlaces_omitidos,
                "carpetas_sin_recorrer": self.escaneo.carpetas_sin_recorrer,
                "carpetas_de_sistema_omitidas": self.escaneo.carpetas_de_sistema_omitidas,
                "errores": self.escaneo.errores,
                "bytes_aceptados": self.escaneo.bytes_aceptados,
            },
            "carpetas_intactas": [str(u) for u in self.escaneo.unidades],
            "duplicados": {
                "candidatos_byte": self.duplicados.candidatos_byte,
                "hasheados_byte": self.duplicados.hasheados_byte,
                "candidatos_pixel": self.duplicados.candidatos_pixel,
                "hasheados_pixel": self.duplicados.hasheados_pixel,
                "descartados_prefiltro": self.duplicados.descartados_prefiltro,
                "grupos": self.duplicados.grupos,
                "copias": self.duplicados.duplicados,
                "bytes_recuperables": self.duplicados.bytes_recuperables,
                "errores": len(self.duplicados.errores),
            },
            "plan": {
                "movimientos": self.plan.total,
                "ya_ordenados": self.plan.ya_ordenados,
                "descartados": self.plan.descartados,
                "a_borrar": self.plan.duplicados,
                "bytes_a_mover": self.plan.bytes_a_mover,
                "bytes_duplicados": self.plan.bytes_duplicados,
                "advertencias": self.plan.advertencias[:50],
            },
            "ejecucion": {
                "aplicados": self.ejecucion.aplicados,
                "omitidos": self.ejecucion.omitidos,
                "fallidos": self.ejecucion.fallidos,
                "bytes_movidos": self.ejecucion.bytes_movidos,
                "carpetas_creadas": self.ejecucion.carpetas_creadas,
                "errores": self.ejecucion.errores[:50],
            },
            "distribucion": {
                "por_tipo": self.conteo("tipo"),
                "por_categoria": self.conteo("categoria"),
                "por_subtipo": self.conteo("subtipo"),
                "por_fuente": self.conteo("fuente"),
                "por_origen_fecha": self.conteo("origen_fecha"),
                "por_anio": self.conteo_anios(),
            },
            "reportes": {
                "csv": str(self.csv) if self.csv else None,
                "manifiesto": str(self.manifiesto) if self.manifiesto else None,
            },
        }


def escribir_csv(plan: Plan, ruta: Path, raiz: Path) -> Path | None:
    """Vuelca el plan completo a CSV. Devuelve la ruta escrita o `None`.

    Las rutas se guardan relativas a `raiz` cuando es posible, para que el
    reporte siga siendo legible si la carpeta se mueve de disco.
    """
    try:
        asegurar_directorio(ruta.parent)
        with open(ruta_del_sistema(ruta), "w", encoding="utf-8-sig", newline="") as manejador:
            escritor = csv.DictWriter(manejador, fieldnames=_CAMPOS_CSV, delimiter=";")
            escritor.writeheader()
            for movimiento in plan.movimientos:
                escritor.writerow(
                    {
                        "origen": _neutralizar(_relativa(movimiento.origen, raiz)),
                        "destino": _neutralizar(_relativa(movimiento.destino, raiz)),
                        "carpeta": _neutralizar(_relativa(movimiento.destino.parent, raiz)),
                        "tipo": _neutralizar(movimiento.tipo),
                        "categoria": _neutralizar(movimiento.categoria),
                        "subtipo": _neutralizar(movimiento.subtipo),
                        "fuente": _neutralizar(movimiento.fuente),
                        "fecha": movimiento.fecha_iso or "",
                        "origen_fecha": _neutralizar(movimiento.origen_fecha),
                        "motivo": _neutralizar(movimiento.motivo),
                        "tamano_bytes": movimiento.tamano,
                    }
                )
        return ruta
    except OSError as exc:
        _log.warning("No se pudo escribir el CSV %s: %s", ruta, exc)
        return None


def escribir_json(informe: Informe, ruta: Path) -> Path | None:
    """Vuelca el resumen a JSON. Devuelve la ruta escrita o `None`."""
    try:
        asegurar_directorio(ruta.parent)
        with open(ruta_del_sistema(ruta), "w", encoding="utf-8") as manejador:
            json.dump(informe.a_dict(), manejador, ensure_ascii=False, indent=2)
        return ruta
    except OSError as exc:
        _log.warning("No se pudo escribir el JSON %s: %s", ruta, exc)
        return None


def _relativa(ruta: Path, raiz: Path) -> str:
    """Ruta relativa a `raiz`, o absoluta si no esta contenida."""
    try:
        return str(ruta.relative_to(raiz))
    except ValueError:
        return str(ruta)


def _absoluta(texto: str, raiz: Path) -> Path | None:
    """Rehidrata una ruta del CSV. `None` si el texto no es admisible.

    Solo se aceptan dos formas: relativa pura, o absoluta completa. Las formas
    hibridas de Windows se rechazan porque `pathlib` las re-ancla de maneras
    sorprendentes: `Path(r'\\Windows\\x')` no es absoluta (le falta la unidad) y
    al unirla a la raiz se queda con la unidad de la raiz pero descarta el
    resto; `Path('C:notas.txt')` es relativa al directorio actual de esa unidad.

    El resultado se normaliza para que un `..` no sobreviva hasta el control de
    contencion.
    """
    if not texto:
        return None
    ruta = Path(texto)
    if ruta.is_absolute():
        return normalizar(ruta)
    if ruta.drive or ruta.root:
        return None  # forma hibrida de Windows: `C:algo` o `\algo`
    return normalizar(raiz / ruta)


def leer_csv(ruta: Path, cfg: Configuracion) -> Plan:
    """Reconstruye un `Plan` desde un CSV generado por `escribir_csv`.

    Permite calcular el plan una vez en simulacion (lo caro: hashear cientos
    de gigabytes) y aplicarlo despues sin repetir el analisis.

    Cada fila se valida antes de aceptarse: el origen debe estar dentro de la
    raiz y el destino dentro del arbol de salida. Una fila manipulada para
    escribir fuera del arbol se descarta y se reporta como advertencia.

    Args:
        ruta: CSV de un plan previo.
        cfg: Configuracion de la corrida que lo aplicara. Aporta la raiz y el
            destino contra los que se valida cada fila.

    Returns:
        El `Plan` reconstruido.

    Raises:
        ConfiguracionInvalida: Si el archivo no existe o no tiene las columnas
            esperadas.
    """
    try:
        with open(ruta_del_sistema(ruta), "r", encoding="utf-8-sig", newline="") as manejador:
            lector = csv.DictReader(manejador, delimiter=";")
            faltantes = _CAMPOS_REQUERIDOS - set(lector.fieldnames or ())
            if faltantes:
                raise ConfiguracionInvalida(
                    f"El CSV {ruta} no tiene las columnas requeridas: {sorted(faltantes)}"
                )
            filas = list(lector)
    except OSError as exc:
        raise ConfiguracionInvalida(f"No se pudo leer el plan {ruta}: {exc}") from exc

    plan = Plan()
    for numero, fila in enumerate(filas, start=2):
        origen = _absoluta(_limpiar(fila.get("origen")), cfg.raiz)
        destino = _absoluta(_limpiar(fila.get("destino")), cfg.raiz)
        if origen is None or destino is None:
            plan.descartados += 1
            plan.advertencias.append(f"Fila {numero}: rutas vacias o no admisibles")
            continue
        if origen == destino:
            plan.ya_ordenados += 1
            continue
        # Ambos extremos se validan. El origen tambien: el CSV es un archivo
        # que la persona abre y edita, y sin este control una fila con un
        # origen arbitrario sacaria del sistema un archivo ajeno al acervo.
        if not es_subruta(origen, cfg.raiz):
            plan.descartados += 1
            plan.advertencias.append(f"Fila {numero}: origen fuera de la raiz -> {origen}")
            continue
        if not es_subruta(destino, cfg.destino_efectivo):
            plan.descartados += 1
            plan.advertencias.append(f"Fila {numero}: destino fuera del arbol -> {destino}")
            continue
        try:
            tamano = int(_limpiar(fila.get("tamano_bytes")) or 0)
        except ValueError:
            tamano = 0

        motivo = _limpiar(fila.get("motivo")) or MOTIVO_ORGANIZAR
        movimiento = Movimiento(
            origen=origen,
            destino=destino,
            tipo=_limpiar(fila.get("tipo")),
            fuente=_limpiar(fila.get("fuente")),
            fecha_iso=_limpiar(fila.get("fecha")) or None,
            origen_fecha=_limpiar(fila.get("origen_fecha")),
            motivo=motivo,
            tamano=tamano,
            categoria=_limpiar(fila.get("categoria")),
            subtipo=_limpiar(fila.get("subtipo")),
        )
        plan.movimientos.append(movimiento)
        plan.bytes_a_mover += tamano
        if movimiento.es_duplicado:
            plan.duplicados += 1
            plan.bytes_duplicados += tamano
    return plan


def _tabla(titulo: str, datos: dict[str, int], limite: int = 12) -> list[str]:
    """Bloque de texto con una distribucion, alineado a la derecha."""
    if not datos:
        return []
    lineas = [f"  {titulo}"]
    total = sum(datos.values()) or 1
    for clave, valor in list(datos.items())[:limite]:
        barra = "#" * max(1, round(28 * valor / total)) if valor else ""
        lineas.append(f"    {clave:<22} {valor:>8,}  {barra}")
    if len(datos) > limite:
        lineas.append(f"    {'(otros)':<22} {sum(list(datos.values())[limite:]):>8,}")
    return lineas


def texto_resumen(informe: Informe) -> str:
    """Construye el resumen legible que se imprime al final de la corrida."""
    cfg = informe.cfg
    plan = informe.plan
    modo = "SIMULACION (no se movio nada)" if cfg.simulacion else f"EJECUCION ({cfg.operacion.value})"
    lineas: list[str] = [
        "",
        "=" * 72,
        f" RESUMEN  -  {modo}",
        "=" * 72,
        f"  Origen              : {cfg.raiz}",
        f"  Destino             : {cfg.destino_efectivo}",
        f"  Modo                : {cfg.modo.value}"
        f"   (profundidad: {cfg.profundidad or 'sin limite'})",
        f"  Jerarquia media     : {cfg.jerarquia.value}",
        f"  Jerarquia archivos  : {cfg.jerarquia_archivos.value}",
        f"  Duplicados          : {cfg.duplicados.value}",
        f"  Duracion            : {formatear_duracion(informe.segundos)}",
        "",
        f"  Archivos vistos     : {informe.escaneo.archivos:,}",
        f"  Aceptados           : {informe.escaneo.aceptados:,}"
        f"  ({formatear_bytes(informe.escaneo.bytes_aceptados)})"
        f"  [media: {informe.escaneo.aceptados_media:,}"
        f" | otros: {informe.escaneo.aceptados_archivos:,}]",
        f"  Ignorados por tipo  : {informe.escaneo.ignorados_extension:,}",
        f"  Carpetas intactas   : {len(informe.escaneo.unidades):,} proyectos"
        f" + {informe.escaneo.carpetas_de_sistema_omitidas:,} de sistema"
        f" + {informe.escaneo.carpetas_sin_recorrer:,} por profundidad",
        f"  Ya ordenados        : {plan.ya_ordenados:,}",
        f"  Movimientos         : {plan.total:,}"
        f"  ({formatear_bytes(plan.bytes_a_mover)})",
        "",
        f"  Grupos duplicados   : {informe.duplicados.grupos:,}",
        f"  Copias a BORRAR     : {plan.duplicados:,}"
        f"  ({formatear_bytes(plan.bytes_duplicados)} recuperables)",
        f"  Hash de bytes       : {informe.duplicados.hasheados_byte:,}"
        f" de {informe.duplicados.candidatos_byte:,} candidatos",
        f"  Hash de pixeles     : {informe.duplicados.hasheados_pixel:,}"
        f" de {informe.duplicados.candidatos_pixel:,} candidatos",
    ]

    if not cfg.simulacion:
        lineas += [
            "",
            f"  Aplicados           : {informe.ejecucion.aplicados:,}"
            f"  ({formatear_bytes(informe.ejecucion.bytes_movidos)})",
            f"  Carpetas creadas    : {informe.ejecucion.carpetas_creadas:,}",
            f"  Fallidos            : {informe.ejecucion.fallidos:,}",
        ]

    lineas.append("")
    lineas += _tabla("Por tipo", informe.conteo("tipo"))
    categorias = {k: v for k, v in informe.conteo("categoria").items() if k}
    if categorias:
        lineas += _tabla("Por categoria", categorias, limite=20)
        subtipos = {k: v for k, v in informe.conteo("subtipo").items() if k}
        lineas += _tabla("Por subtipo", subtipos, limite=20)
    fuentes = {k: v for k, v in informe.conteo("fuente").items() if k}
    lineas += _tabla("Por fuente", fuentes)
    lineas += _tabla("Por anio", informe.conteo_anios(), limite=30)
    lineas += _tabla("Origen de la fecha", informe.conteo("origen_fecha"))

    if informe.escaneo.unidades:
        lineas += ["", f"  Carpetas que se dejaron intactas ({len(informe.escaneo.unidades)}):"]
        for unidad in informe.escaneo.unidades[:10]:
            lineas.append(f"    - {unidad}")
        if len(informe.escaneo.unidades) > 10:
            lineas.append(f"    ... y {len(informe.escaneo.unidades) - 10} mas (ver el JSON)")

    if plan.advertencias:
        lineas += ["", f"  Advertencias ({len(plan.advertencias)}):"]
        lineas += [f"    - {a}" for a in plan.advertencias[:5]]
    if informe.ejecucion.errores:
        lineas += ["", f"  Errores ({informe.ejecucion.fallidos}):"]
        lineas += [f"    - {e}" for e in informe.ejecucion.errores[:5]]

    if informe.csv:
        lineas += ["", f"  Detalle CSV         : {informe.csv}"]
    if informe.manifiesto:
        lineas += [f"  Manifiesto          : {informe.manifiesto}"]
    if cfg.simulacion and plan.total:
        lineas += [
            "",
            "  Nada se movio. Revisa el CSV y vuelve a ejecutar con --ejecutar",
            "  para aplicar exactamente este plan.",
        ]
    lineas += ["=" * 72, ""]
    return "\n".join(lineas)
