"""Manejo defensivo de rutas: saneado, rutas largas de Windows y colisiones.

Todo nombre de carpeta o archivo que el organizador genera pasa por aqui.
El objetivo es que ningun dato derivado de metadatos o de nombres de archivo
del usuario pueda producir una ruta fuera del arbol destino, un nombre
reservado del sistema, o una ruta que el SO rechace.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path, PurePath

from .errores import RutaInsegura

__all__ = [
    "sanear_componente",
    "ruta_del_sistema",
    "es_subruta",
    "normalizar",
    "nombre_disponible",
    "asegurar_directorio",
    "mismo_volumen",
    "LARGO_MAXIMO_COMPONENTE",
]

#: Windows admite 255 caracteres por componente; dejamos margen para sufijos.
LARGO_MAXIMO_COMPONENTE = 180

#: Reservados por el subsistema DOS de Windows, con o sin extension.
_NOMBRES_RESERVADOS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

#: Prohibidos en NTFS mas los de control. Se sustituyen por `_`.
_PROHIBIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_ESPACIOS = re.compile(r"\s+")
_GUIONES = re.compile(r"_{2,}")

_PREFIJO_LARGO = "\\\\?\\"
_PREFIJO_UNC = "\\\\?\\UNC\\"


def sanear_componente(texto: str, *, respaldo: str = "Sin-Nombre") -> str:
    """Convierte texto arbitrario en un componente de ruta valido.

    Args:
        texto: Cadena de origen (fuente detectada, etiqueta de fecha, etc.).
        respaldo: Valor a usar si el saneado deja la cadena vacia.

    Returns:
        Un nombre seguro: sin caracteres prohibidos, sin puntos ni espacios
        al final, no reservado por Windows y de largo acotado.

    Examples:
        >>> sanear_componente('  Fotos: 2024/05 ')
        'Fotos_ 2024_05'
        >>> sanear_componente('CON')
        'CON_'
        >>> sanear_componente('...')
        'Sin-Nombre'
    """
    normalizado = unicodedata.normalize("NFC", texto)
    limpio = _PROHIBIDOS.sub("_", normalizado)
    limpio = _ESPACIOS.sub(" ", limpio).strip()
    limpio = _GUIONES.sub("_", limpio)
    # Windows descarta silenciosamente puntos y espacios finales.
    limpio = limpio.rstrip(". ")
    if len(limpio) > LARGO_MAXIMO_COMPONENTE:
        limpio = limpio[:LARGO_MAXIMO_COMPONENTE].rstrip(". ")
    if not limpio or set(limpio) <= {"_"}:
        return respaldo
    raiz = limpio.split(".", 1)[0].casefold()
    if raiz in _NOMBRES_RESERVADOS:
        return f"{limpio}_"
    return limpio


def ruta_del_sistema(ruta: Path) -> str:
    """Devuelve la cadena a pasar al SO, sorteando el limite MAX_PATH.

    En Windows las rutas de mas de 259 caracteres fallan salvo que se usen
    con el prefijo `\\\\?\\`. El prefijo exige rutas absolutas y normalizadas.
    En otros sistemas devuelve la ruta tal cual.
    """
    if os.name != "nt":
        return str(ruta)
    texto = os.path.abspath(str(ruta))
    if texto.startswith(_PREFIJO_LARGO):
        return texto
    if texto.startswith("\\\\"):
        return _PREFIJO_UNC + texto[2:]
    return _PREFIJO_LARGO + texto


def es_subruta(candidata: PurePath, contenedora: PurePath) -> bool:
    """True si `candidata` esta dentro de `contenedora` (o es ella misma).

    No toca el disco, asi que es seguro llamarla sobre rutas que aun no
    existen. La comparacion es lexica, y por eso una ruta con `..` se rechaza
    de plano en lugar de compararse: `raiz/../../otro` tiene a `raiz` entre sus
    ancestros lexicos y pasaria el control, para despues salir del arbol cuando
    el sistema operativo colapse los `..`.

    Examples:
        >>> from pathlib import PureWindowsPath as P
        >>> es_subruta(P(r'C:\\medios\\Fotos'), P(r'C:\\medios'))
        True
        >>> es_subruta(P(r'C:\\medios\\Fotos\\..\\..\\otro'), P(r'C:\\medios'))
        False
        >>> es_subruta(P(r'C:\\medios2'), P(r'C:\\medios'))
        False
    """
    try:
        if ".." in candidata.parts or ".." in contenedora.parts:
            return False
        return candidata == contenedora or contenedora in candidata.parents
    except (TypeError, ValueError):  # pragma: no cover - rutas incomparables
        return False


def normalizar(ruta: Path) -> Path:
    """Colapsa `..`, `.` y separadores mixtos sin tocar el disco.

    Se usa antes de cualquier comprobacion de contencion sobre rutas que
    provienen de un archivo (un CSV de plan, un manifiesto), donde el texto no
    esta bajo nuestro control.
    """
    return Path(os.path.normpath(str(ruta)))


def _partir_nombre(nombre: str) -> tuple[str, str]:
    """Separa base y extension respetando extensiones compuestas simples."""
    punto = nombre.rfind(".")
    if punto <= 0:
        return nombre, ""
    return nombre[:punto], nombre[punto:]


def nombre_disponible(destino: Path, reservados: set[Path]) -> Path:
    """Encuentra una ruta libre agregando sufijos `~1`, `~2`, ...

    Considera ocupado tanto lo que existe en disco como lo que otro
    movimiento del mismo plan ya reservo. Esto evita que dos archivos
    distintos aterricen en la misma ruta durante una corrida.

    Raises:
        RutaInsegura: Si tras 10.000 intentos no hay nombre libre.
    """
    if destino not in reservados and not _existe(destino):
        return destino
    base, extension = _partir_nombre(destino.name)
    for indice in range(1, 10_000):
        candidata = destino.with_name(f"{base}~{indice}{extension}")
        if candidata not in reservados and not _existe(candidata):
            return candidata
    raise RutaInsegura(f"No se encontro un nombre libre para {destino}")


def _existe(ruta: Path) -> bool:
    """`exists()` tolerante a rutas largas y a permisos denegados."""
    try:
        return os.path.lexists(ruta_del_sistema(ruta))
    except (OSError, ValueError):  # pragma: no cover - defensivo
        return False


def asegurar_directorio(ruta: Path) -> None:
    """Crea la carpeta y sus padres si faltan.

    Raises:
        RutaInsegura: Si el SO rechaza la creacion.
    """
    try:
        os.makedirs(ruta_del_sistema(ruta), exist_ok=True)
    except OSError as exc:
        raise RutaInsegura(f"No se pudo crear la carpeta {ruta}: {exc}") from exc


def mismo_volumen(a: Path, b: Path) -> bool:
    """Heuristica de volumen para decidir entre `os.replace` y `shutil.move`.

    En Windows compara la letra de unidad; en POSIX compara `st_dev` del
    primer ancestro existente de cada ruta.
    """
    if os.name == "nt":
        return os.path.splitdrive(str(a))[0].casefold() == os.path.splitdrive(str(b))[0].casefold()
    try:
        return _dispositivo(a) == _dispositivo(b)
    except OSError:  # pragma: no cover - defensivo
        return False


def _dispositivo(ruta: Path) -> int:
    """`st_dev` del ancestro existente mas cercano."""
    actual = ruta
    while not actual.exists() and actual != actual.parent:
        actual = actual.parent
    return actual.stat().st_dev
