"""Deteccion de duplicados en dos niveles, con union de componentes.

Estrategia y por que:

1. **Filtro por tamano.** Dos archivos byte-identicos tienen el mismo tamano.
   Solo se hashean los archivos cuyo tamano colisiona con el de otro. En un
   acervo de 280 GB esto evita leer la mayor parte del disco.
2. **Huella de bytes.** BLAKE2b sobre los candidatos del paso 1.
3. **Filtro por firma de imagen.** Dos imagenes identicas pixel a pixel
   comparten ancho, alto, modo y numero de cuadros. Solo se decodifican las
   imagenes cuya firma colisiona.
4. **Huella de pixeles.** Solo sobre un representante por componente ya
   unificado, de modo que ninguna imagen se decodifica dos veces.

Los pasos 2 y 4 unen componentes en una estructura union-find, asi que un
grupo puede formarse por transitividad: si A=B por bytes y B=C por pixeles,
el grupo final es {A, B, C}.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .config import Configuracion, ModoDuplicados
from .escaneo import ArchivoEncontrado
from .huellas import (
    ResultadoHuella,
    huella_de_bytes,
    huella_de_pixeles,
    huella_rapida_jpeg,
)
from .metadatos import Analisis
from .paralelo import mapear
from .rutas_seguras import ruta_del_sistema

__all__ = ["MotivoDuplicado", "GrupoDuplicados", "ResumenDuplicados", "detectar_duplicados"]

_log = logging.getLogger(__name__)

#: Extensiones sobre las que aplica el prefiltro DCT.
_EXTENSIONES_JPEG = frozenset({".jpg", ".jpeg", ".jpe", ".jfif"})


class MotivoDuplicado(str, Enum):
    """Como se probo que los archivos del grupo son el mismo contenido."""

    BYTE = "copia-exacta"
    PIXEL = "copia-pixel"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True, slots=True)
class GrupoDuplicados:
    """Conjunto de archivos con contenido identico."""

    motivo: MotivoDuplicado
    integrantes: tuple[ArchivoEncontrado, ...]

    def __post_init__(self) -> None:
        if len(self.integrantes) < 2:  # pragma: no cover - invariante interna
            raise ValueError("Un grupo de duplicados requiere al menos dos integrantes")


@dataclass(slots=True)
class ResumenDuplicados:
    """Estadisticas de la deteccion."""

    candidatos_byte: int = 0
    hasheados_byte: int = 0
    candidatos_pixel: int = 0
    hasheados_pixel: int = 0
    descartados_prefiltro: int = 0
    grupos: int = 0
    duplicados: int = 0
    #: Cota inferior: descuenta el integrante mas grande de cada grupo. El
    #: espacio exacto que libera la corrida es `plan.bytes_duplicados`.
    bytes_recuperables: int = 0
    errores: list[str] = field(default_factory=list)


class _Conjuntos:
    """Union-find con compresion de caminos. Indices enteros, sin recursion."""

    __slots__ = ("_padre", "_rango")

    def __init__(self, cantidad: int) -> None:
        self._padre = list(range(cantidad))
        self._rango = [0] * cantidad

    def raiz(self, indice: int) -> int:
        """Representante del componente de `indice`."""
        padre = self._padre
        while padre[indice] != indice:
            padre[indice] = padre[padre[indice]]
            indice = padre[indice]
        return indice

    def unir(self, a: int, b: int) -> bool:
        """Une dos componentes. Devuelve False si ya eran el mismo."""
        ra, rb = self.raiz(a), self.raiz(b)
        if ra == rb:
            return False
        if self._rango[ra] < self._rango[rb]:
            ra, rb = rb, ra
        self._padre[rb] = ra
        if self._rango[ra] == self._rango[rb]:
            self._rango[ra] += 1
        return True

    def componentes(self) -> dict[int, list[int]]:
        """Agrupa todos los indices por representante."""
        grupos: dict[int, list[int]] = defaultdict(list)
        for indice in range(len(self._padre)):
            grupos[self.raiz(indice)].append(indice)
        return grupos


def detectar_duplicados(
    items: Sequence[ArchivoEncontrado],
    analisis: Mapping[Path, Analisis],
    cfg: Configuracion,
) -> tuple[list[GrupoDuplicados], ResumenDuplicados]:
    """Encuentra grupos de archivos con contenido identico.

    Args:
        items: Archivos aceptados por el escaneo.
        analisis: Metadatos por ruta (aporta la firma de imagen del prefiltro).
        cfg: Configuracion; define la profundidad de la deteccion.

    Returns:
        `(grupos, resumen)`. Los grupos nunca se solapan y todos tienen al
        menos dos integrantes. La eleccion del superviviente NO se hace aqui:
        es politica del planificador.
    """
    resumen = ResumenDuplicados()
    if cfg.duplicados is ModoDuplicados.NINGUNO or len(items) < 2:
        return [], resumen

    conjuntos = _Conjuntos(len(items))
    # Indices involucrados en al menos una union por pixeles. Se guardan
    # indices y no raices porque la raiz de un componente puede cambiar con
    # uniones posteriores.
    por_pixel: set[int] = set()

    _unir_por_bytes(items, conjuntos, cfg, resumen)
    if cfg.duplicados is ModoDuplicados.PIXEL:
        _unir_por_pixeles(items, analisis, conjuntos, cfg, resumen, por_pixel)

    grupos: list[GrupoDuplicados] = []
    for indices in conjuntos.componentes().values():
        if len(indices) < 2:
            continue
        motivo = (
            MotivoDuplicado.PIXEL
            if any(indice in por_pixel for indice in indices)
            else MotivoDuplicado.BYTE
        )
        integrantes = tuple(items[i] for i in sorted(indices, key=lambda i: str(items[i].ruta)))
        grupos.append(GrupoDuplicados(motivo, integrantes))
        resumen.grupos += 1
        resumen.duplicados += len(integrantes) - 1
        # Cota inferior del espacio recuperable: se descuenta el integrante mas
        # grande, que es el candidato mas probable a sobrevivir. El numero
        # exacto lo da `plan.bytes_duplicados`, porque la eleccion del
        # superviviente es politica del planificador, no de esta capa.
        tamanos = sorted((x.tamano for x in integrantes), reverse=True)
        resumen.bytes_recuperables += sum(tamanos[1:])

    grupos.sort(key=lambda g: str(g.integrantes[0].ruta))
    return grupos, resumen


# --------------------------------------------------------------------------- #
# Paso 1-2: bytes
# --------------------------------------------------------------------------- #

def _unir_por_bytes(
    items: Sequence[ArchivoEncontrado],
    conjuntos: _Conjuntos,
    cfg: Configuracion,
    resumen: ResumenDuplicados,
) -> None:
    """Une archivos con el mismo tamano y la misma huella de bytes."""
    por_tamano: dict[int, list[int]] = defaultdict(list)
    for indice, item in enumerate(items):
        por_tamano[item.tamano].append(indice)

    candidatos = [i for grupo in por_tamano.values() if len(grupo) > 1 for i in grupo]
    resumen.candidatos_byte = len(candidatos)
    if not candidatos:
        return

    huellas = _calcular(items, candidatos, huella_de_bytes, cfg, "hash bytes ", resumen)
    resumen.hasheados_byte = len(huellas)

    cubos: dict[tuple[int, str], list[int]] = defaultdict(list)
    for indice, huella in huellas.items():
        cubos[(items[indice].tamano, huella)].append(indice)
    for grupo in cubos.values():
        for otro in grupo[1:]:
            conjuntos.unir(grupo[0], otro)


# --------------------------------------------------------------------------- #
# Paso 3-4: pixeles
# --------------------------------------------------------------------------- #

def _unir_por_pixeles(
    items: Sequence[ArchivoEncontrado],
    analisis: Mapping[Path, Analisis],
    conjuntos: _Conjuntos,
    cfg: Configuracion,
    resumen: ResumenDuplicados,
    marcados_pixel: set[int],
) -> None:
    """Une imagenes identicas pixel a pixel aunque sus bytes difieran."""
    # Un representante por componente: evita decodificar dos veces una imagen
    # que ya se sabe identica a otra.
    representantes: dict[int, int] = {}
    for indice, item in enumerate(items):
        if not item.tipo.es_imagen:
            continue
        raiz = conjuntos.raiz(indice)
        representantes.setdefault(raiz, indice)

    por_firma: dict[tuple[int, int, str, int], list[int]] = defaultdict(list)
    for indice in representantes.values():
        info = analisis.get(items[indice].ruta)
        if info is None or info.firma is None:
            continue
        por_firma[info.firma.clave].append(indice)

    candidatos = [i for grupo in por_firma.values() if len(grupo) > 1 for i in grupo]
    resumen.candidatos_pixel = len(candidatos)
    if not candidatos:
        return

    if cfg.prefiltro_rapido:
        candidatos = _aplicar_prefiltro(items, por_firma, cfg, resumen)
        if not candidatos:
            return

    huellas = _calcular(items, candidatos, huella_de_pixeles, cfg, "hash pixeles", resumen)
    resumen.hasheados_pixel = len(huellas)

    cubos: dict[str, list[int]] = defaultdict(list)
    for indice, huella in huellas.items():
        cubos[huella].append(indice)
    for grupo in cubos.values():
        for otro in grupo[1:]:
            if conjuntos.unir(grupo[0], otro):
                marcados_pixel.add(grupo[0])
                marcados_pixel.add(otro)


def _aplicar_prefiltro(
    items: Sequence[ArchivoEncontrado],
    por_firma: Mapping[tuple[int, int, str, int], list[int]],
    cfg: Configuracion,
    resumen: ResumenDuplicados,
) -> list[int]:
    """Reduce candidatos con el escalado DCT de JPEG (opcional, `--prefiltro`).

    Solo descarta: dos archivos que sobreviven al prefiltro siguen pasando por
    la comparacion exacta de pixeles.
    """
    def es_jpeg(indice: int) -> bool:
        return items[indice].ruta.suffix.casefold() in _EXTENSIONES_JPEG

    grupos = [grupo for grupo in por_firma.values() if len(grupo) > 1]
    # Un grupo mixto pasa completo: comparar la huella rapida de un JPEG contra
    # la de un PNG no tiene sentido, asi que el prefiltro no puede opinar sobre
    # ese grupo y descartar su unico JPEG romperia la deteccion del par.
    directos = [i for grupo in grupos if any(not es_jpeg(i) for i in grupo) for i in grupo]
    solo_jpeg = [grupo for grupo in grupos if all(es_jpeg(i) for i in grupo)]
    elegibles = [i for grupo in solo_jpeg for i in grupo]
    if not elegibles:
        return directos

    rapidas = _calcular(items, elegibles, huella_rapida_jpeg, cfg, "prefiltro   ", resumen)
    # La comparacion es POR GRUPO de firma: dos JPEG de grupos distintos no son
    # candidatos entre si, asi que compartir huella rapida no los salva.
    sobreviven: list[int] = []
    for grupo in solo_jpeg:
        cubos: dict[str, list[int]] = defaultdict(list)
        sin_huella: list[int] = []
        for indice in grupo:
            huella = rapidas.get(indice)
            if huella is None:
                sin_huella.append(indice)  # fallo el prefiltro: no se descarta
            else:
                cubos[huella].append(indice)
        sobreviven.extend(i for iguales in cubos.values() if len(iguales) > 1 for i in iguales)
        sobreviven.extend(sin_huella)
    resumen.descartados_prefiltro = len(elegibles) - len(sobreviven)
    return sobreviven + directos


# --------------------------------------------------------------------------- #
# Utilidad comun
# --------------------------------------------------------------------------- #

def _calcular(
    items: Sequence[ArchivoEncontrado],
    indices: Sequence[int],
    funcion: Callable[[str], ResultadoHuella],
    cfg: Configuracion,
    etiqueta: str,
    resumen: ResumenDuplicados,
) -> dict[int, str]:
    """Ejecuta una funcion de huella en paralelo y mapea el resultado a indices.

    Las rutas viajan como texto listo para el SO; el diccionario inverso
    reconstruye el indice original sin depender del orden de los resultados.
    """
    rutas = [ruta_del_sistema(items[i].ruta) for i in indices]
    inverso = {ruta: indice for ruta, indice in zip(rutas, indices, strict=True)}
    salida: dict[int, str] = {}
    resultado: ResultadoHuella
    for resultado in mapear(funcion, rutas, procesos=cfg.procesos, etiqueta=etiqueta):
        indice = inverso.get(resultado.ruta)
        if indice is None:  # pragma: no cover - defensivo
            continue
        if resultado.huella is None:
            mensaje = f"{items[indice].ruta}: {resultado.error}"
            resumen.errores.append(mensaje)
            _log.debug("Huella fallida %s", mensaje)
            continue
        salida[indice] = resultado.huella
    return salida
