"""Resolucion de la fecha de captura de un archivo audiovisual.

Cascada de confianza, de mas fiable a menos:

1. `EXIF` DateTimeOriginal / DateTimeDigitized / DateTime (fotos).
2. Contenedor ISO-BMFF: `moov/mvhd.creation_time` y `moov/udta/@day` (videos).
3. Patron de fecha en el nombre del archivo.
4. Fecha de modificacion del sistema de archivos.

El muestreo de la carpeta real mostro EXIF en solo ~26% de las fotos y
`creation_time = 0` en los videos de mensajeria, por lo que los pasos 3 y 4
no son casos raros: son la ruta principal para la mayoria del acervo.
"""

from __future__ import annotations

import logging
import os
import re
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import BinaryIO, Final

from PIL import Image

from .config import LIMITE_ANIO_MINIMO
from .huellas import ERRORES_IMAGEN
from .rutas_seguras import ruta_del_sistema
from .tipos import TipoMedia

__all__ = [
    "OrigenFecha",
    "FechaMedia",
    "fecha_desde_nombre",
    "fecha_desde_exif",
    "fecha_desde_imagen",
    "fecha_desde_contenedor",
    "fecha_desde_sistema",
    "continuar_cascada",
    "resolver_fecha",
    "etiqueta_anio",
    "etiqueta_mes",
]

_log = logging.getLogger(__name__)

#: Etiquetas EXIF relevantes (valores numericos del estandar).
_EXIF_IFD: Final = 0x8769
_DATETIME_ORIGINAL: Final = 0x9003
_DATETIME_DIGITIZED: Final = 0x9004
_DATETIME: Final = 0x0132

#: ISO-BMFF cuenta segundos desde 1904-01-01 UTC.
_EPOCA_BMFF: Final = datetime(1904, 1, 1, tzinfo=timezone.utc)

#: Topes para no permitir que un archivo corrupto agote CPU o memoria.
_MAX_CAJAS: Final = 4096
_MAX_PROFUNDIDAD: Final = 6

_MESES_ES: Final = (
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
)

#: Nombres que son un volcado hexadecimal (hash de app). No contienen fechas.
_BLOB_HEX = re.compile(r"^[0-9a-f]{24,}", re.IGNORECASE)


class OrigenFecha(str, Enum):
    """De donde salio la fecha. Se reporta para poder auditar el resultado."""

    EXIF = "exif"
    CONTENEDOR = "contenedor"
    #: Metadatos internos de un documento (OOXML, OpenDocument, PDF).
    DOCUMENTO = "documento"
    NOMBRE = "nombre"
    SISTEMA = "sistema"
    DESCONOCIDO = "desconocido"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True, slots=True)
class FechaMedia:
    """Fecha resuelta junto con su procedencia."""

    valor: datetime | None
    origen: OrigenFecha

    @property
    def confiable(self) -> bool:
        """True si la fecha proviene de metadatos o del nombre, no del `mtime`."""
        return self.origen in (
            OrigenFecha.EXIF,
            OrigenFecha.CONTENEDOR,
            OrigenFecha.DOCUMENTO,
            OrigenFecha.NOMBRE,
        )


_SIN_FECHA: Final = FechaMedia(None, OrigenFecha.DESCONOCIDO)


def _valida(momento: datetime | None) -> datetime | None:
    """Descarta fechas imposibles (relojes en cero, futuros, anios absurdos)."""
    if momento is None:
        return None
    if momento.tzinfo is not None:
        momento = momento.astimezone().replace(tzinfo=None)
    if momento.year < LIMITE_ANIO_MINIMO:
        return None
    if momento > datetime.now() + timedelta(days=2):
        return None
    return momento


def _construir(
    anio: int, mes: int, dia: int, hora: int = 12, minuto: int = 0, segundo: int = 0
) -> datetime | None:
    """`datetime` tolerante: devuelve `None` en lugar de lanzar `ValueError`."""
    try:
        return _valida(datetime(anio, mes, dia, hora, minuto, segundo))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# 1. Nombre de archivo
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, slots=True)
class _ReglaNombre:
    """Patron de fecha en el nombre y como interpretar sus grupos."""

    patron: re.Pattern[str]
    forma: str  # "compacta", "iso", "epoca_ms"


#: Orden = prioridad. Lo mas especifico primero para evitar coincidencias
#: parciales de las reglas genericas.
_REGLAS_NOMBRE: Final[tuple[_ReglaNombre, ...]] = (
    # IMG-20240512-WA0003 / VID-20221020-WA0039  (mensajeria: solo fecha)
    _ReglaNombre(re.compile(r"(?:IMG|VID|AUD|DOC)[-_](\d{8})[-_]WA\d+", re.I), "compacta"),
    # Screenshot_20240608_193933_WhatsApp / Screen_Recording_20240808-203118
    _ReglaNombre(
        re.compile(r"(?:screenshot|screen[_-]?recording|captura)\w*?[_-](\d{8})[_-](\d{6})", re.I),
        "compacta",
    ),
    # IMG_20240318_161153 / PXL_20240512_143022 / VID_20241209_120302 / DSC_...
    _ReglaNombre(
        re.compile(
            r"(?:IMG|VID|PXL|PANO|BURST|MVIMG|TRIM|DSC|DSCN|SAVE|penup|resize)"
            r"\w*?[_-](\d{8})[_-](\d{6})",
            re.I,
        ),
        "compacta",
    ),
    # 20230726_124814  (patron dominante de camara Samsung)
    _ReglaNombre(re.compile(r"(?<!\d)(\d{8})[_\-.](\d{6})(?!\d)"), "compacta"),
    # WhatsApp Image 2024-05-12 at 14.30.22 / 2024-05-12 14_30_22
    _ReglaNombre(
        re.compile(
            r"(?<!\d)(\d{4})[-.](\d{2})[-.](\d{2})"
            r"(?:[ _T]+(?:at[ _]+)?(\d{2})[-.:_](\d{2})(?:[-.:_](\d{2}))?)?(?!\d)",
            re.I,
        ),
        "iso",
    ),
    # FB_IMG_1704399976041 / received_1561306096025  (milisegundos de epoca)
    _ReglaNombre(
        re.compile(r"(?:FB_IMG|received|image|video|img|vid|photo)[-_](\d{13})(?!\d)", re.I),
        "epoca_ms",
    ),
    # 20230726  (ultimo recurso: solo fecha, sin hora)
    _ReglaNombre(re.compile(r"(?<!\d)(\d{8})(?!\d)"), "compacta"),
)


def fecha_desde_nombre(nombre: str) -> datetime | None:
    """Extrae una fecha del nombre de archivo.

    Nunca interpreta un numero suelto como epoca en segundos: los IDs de
    galeria tipo `1000035167.jpg` son de 10 digitos y se leerian como 2001.
    Solo se acepta epoca cuando viene en milisegundos y precedida por un
    prefijo conocido.

    Args:
        nombre: Nombre del archivo, con o sin extension.

    Returns:
        La fecha detectada, o `None` si no hay ninguna plausible.

    Examples:
        >>> fecha_desde_nombre('IMG-20190606-WA0044.jpg').date().isoformat()
        '2019-06-06'
        >>> fecha_desde_nombre('20230726_124814.jpg').isoformat()
        '2023-07-26T12:48:14'
        >>> fecha_desde_nombre('1000035167.jpg') is None
        True
    """
    if _BLOB_HEX.match(nombre):
        return None
    for regla in _REGLAS_NOMBRE:
        coincidencia = regla.patron.search(nombre)
        if coincidencia is None:
            continue
        momento = _interpretar(coincidencia, regla.forma)
        if momento is not None:
            return momento
    return None


def _interpretar(coincidencia: re.Match[str], forma: str) -> datetime | None:
    """Traduce los grupos capturados a `datetime` segun la forma de la regla."""
    grupos = coincidencia.groups()
    if forma == "compacta":
        crudo = grupos[0]
        anio, mes, dia = int(crudo[0:4]), int(crudo[4:6]), int(crudo[6:8])
        if len(grupos) > 1 and grupos[1]:
            reloj = grupos[1]
            return _construir(anio, mes, dia, int(reloj[0:2]), int(reloj[2:4]), int(reloj[4:6]))
        return _construir(anio, mes, dia)
    if forma == "iso":
        anio, mes, dia = int(grupos[0]), int(grupos[1]), int(grupos[2])
        hora = int(grupos[3]) if len(grupos) > 3 and grupos[3] else 12
        minuto = int(grupos[4]) if len(grupos) > 4 and grupos[4] else 0
        segundo = int(grupos[5]) if len(grupos) > 5 and grupos[5] else 0
        return _construir(anio, mes, dia, hora, minuto, segundo)
    if forma == "epoca_ms":
        try:
            return _valida(datetime.fromtimestamp(int(grupos[0]) / 1000.0))
        except (ValueError, OSError, OverflowError):
            return None
    return None  # pragma: no cover - forma desconocida


# --------------------------------------------------------------------------- #
# 2. EXIF
# --------------------------------------------------------------------------- #

def _parsear_exif(texto: object) -> datetime | None:
    """Convierte `'2023:02:25 16:42:08'` a `datetime`."""
    if not isinstance(texto, (str, bytes)):
        return None
    cadena = texto.decode("ascii", "ignore") if isinstance(texto, bytes) else texto
    cadena = cadena.replace("\x00", "").strip()
    if not cadena or cadena.startswith("0000"):
        return None
    for formato in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return _valida(datetime.strptime(cadena[:19], formato))
        except ValueError:
            continue
    return None


def fecha_desde_imagen(imagen: Image.Image) -> datetime | None:
    """Extrae la fecha EXIF de una imagen ya abierta.

    Existe como funcion aparte para que quien ya tenga la imagen abierta (por
    ejemplo para leer sus dimensiones) no pague una segunda apertura. En un
    disco USB la primera apertura de cada archivo cuesta ~100 ms, asi que
    compartirla importa.
    """
    try:
        exif = imagen.getexif()
        if not exif:
            return None
        subifd = exif.get_ifd(_EXIF_IFD)
        for etiqueta in (_DATETIME_ORIGINAL, _DATETIME_DIGITIZED):
            momento = _parsear_exif(subifd.get(etiqueta))
            if momento is not None:
                return momento
        return _parsear_exif(exif.get(_DATETIME))
    except ERRORES_IMAGEN as exc:
        _log.debug("EXIF ilegible: %s", exc)
        return None


def fecha_desde_exif(ruta: Path) -> datetime | None:
    """Lee la fecha de captura EXIF abriendo el archivo.

    Devuelve `None` si el archivo no es imagen, no tiene EXIF, o el EXIF esta
    corrupto. Nunca propaga excepciones de Pillow.
    """
    try:
        with Image.open(ruta_del_sistema(ruta)) as imagen:
            return fecha_desde_imagen(imagen)
    except ERRORES_IMAGEN as exc:
        _log.debug("EXIF ilegible en %s: %s", ruta.name, exc)
        return None


# --------------------------------------------------------------------------- #
# 3. Contenedor ISO-BMFF (mp4 / mov / 3gp / m4v)
# --------------------------------------------------------------------------- #

def _leer_encabezado_caja(manejador: BinaryIO, limite: int) -> tuple[bytes, int, int] | None:
    """Lee un encabezado de caja BMFF.

    Returns:
        `(tipo, inicio_datos, fin_caja)` o `None` al terminar o ante datos
        invalidos. Soporta `largesize` de 64 bits y `size == 0` (hasta el fin).
    """
    inicio = manejador.tell()
    if inicio + 8 > limite:
        return None
    cabecera = manejador.read(8)
    if len(cabecera) < 8:
        return None
    tamano = struct.unpack(">I", cabecera[0:4])[0]
    tipo = cabecera[4:8]
    datos = inicio + 8
    if tamano == 1:
        extra = manejador.read(8)
        if len(extra) < 8:
            return None
        tamano = struct.unpack(">Q", extra)[0]
        datos = inicio + 16
    elif tamano == 0:
        return tipo, datos, limite
    fin = inicio + tamano
    if tamano < 8 or fin > limite or fin <= inicio:
        return None
    return tipo, datos, fin


def _buscar_en_moov(
    manejador: BinaryIO, inicio: int, fin: int, profundidad: int, presupuesto: list[int]
) -> datetime | None:
    """Recorre recursivamente el arbol de cajas buscando la fecha de creacion."""
    if profundidad > _MAX_PROFUNDIDAD:
        return None
    manejador.seek(inicio)
    respaldo: datetime | None = None
    while manejador.tell() < fin:
        presupuesto[0] -= 1
        if presupuesto[0] <= 0:
            return respaldo
        caja = _leer_encabezado_caja(manejador, fin)
        if caja is None:
            return respaldo
        tipo, datos, limite_caja = caja
        if tipo == b"mvhd":
            momento = _leer_mvhd(manejador, datos, limite_caja)
            if momento is not None:
                return momento
        elif tipo in (b"\xa9day", b"date"):
            momento = _leer_texto_fecha(manejador, datos, limite_caja)
            if momento is not None and respaldo is None:
                respaldo = momento
        elif tipo in (b"moov", b"udta", b"meta", b"ilst", b"trak", b"mdia"):
            momento = _buscar_en_moov(manejador, datos, limite_caja, profundidad + 1, presupuesto)
            if momento is not None:
                return momento
        manejador.seek(limite_caja)
    return respaldo


def _leer_mvhd(manejador: BinaryIO, inicio: int, fin: int) -> datetime | None:
    """Extrae `creation_time` de una caja `mvhd`."""
    manejador.seek(inicio)
    cabecera = manejador.read(min(20, max(0, fin - inicio)))
    if len(cabecera) < 5:
        return None
    version = cabecera[0]
    cuerpo = cabecera[4:]
    try:
        if version == 1:
            if len(cuerpo) < 8:
                return None
            segundos = struct.unpack(">Q", cuerpo[0:8])[0]
        else:
            if len(cuerpo) < 4:
                return None
            segundos = struct.unpack(">I", cuerpo[0:4])[0]
    except struct.error:  # pragma: no cover - defensivo
        return None
    if segundos <= 0:
        return None  # mensajeria suele dejar el campo en cero
    try:
        return _valida(_EPOCA_BMFF + timedelta(seconds=segundos))
    except (OverflowError, OSError, ValueError):
        return None


def _leer_texto_fecha(manejador: BinaryIO, inicio: int, fin: int) -> datetime | None:
    """Interpreta cajas de texto tipo `@day` con una fecha ISO 8601."""
    manejador.seek(inicio)
    crudo = manejador.read(min(64, max(0, fin - inicio)))
    texto = crudo.decode("utf-8", "ignore")
    coincidencia = re.search(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})", texto)
    if coincidencia:
        numeros = [int(g) for g in coincidencia.groups()]
        return _construir(*numeros)
    coincidencia = re.search(r"(\d{4})-(\d{2})-(\d{2})", texto)
    if coincidencia:
        return _construir(*(int(g) for g in coincidencia.groups()))
    return None


def fecha_desde_contenedor(ruta: Path) -> datetime | None:
    """Lee la fecha de creacion de un contenedor ISO-BMFF.

    Recorre el arbol de cajas por desplazamientos, no buscando cadenas: en
    los MP4 sin `faststart` la caja `moov` esta al final del archivo y una
    busqueda por texto sobre los primeros megabytes no la encuentra.
    """
    try:
        with open(ruta_del_sistema(ruta), "rb") as manejador:
            manejador.seek(0, 2)
            total = manejador.tell()
            if total < 16:
                return None
            manejador.seek(0)
            presupuesto = [_MAX_CAJAS]
            return _buscar_en_moov(manejador, 0, total, 0, presupuesto)
    except (OSError, struct.error, ValueError) as exc:
        _log.debug("Contenedor ilegible en %s: %s", ruta.name, exc)
        return None


# --------------------------------------------------------------------------- #
# 4. Sistema de archivos
# --------------------------------------------------------------------------- #

def fecha_desde_sistema(
    ruta: Path, mtime: float | None = None, nacimiento: float | None = None
) -> datetime | None:
    """Usa la fecha del sistema de archivos como ultimo recurso.

    Toma la **mas antigua** entre la fecha de modificacion y la de creacion.
    En un archivo copiado a un disco de respaldo la fecha de creacion es la de
    la copia y la de modificacion suele conservar la original, asi que la mas
    antigua es la que se parece mas a la fecha de captura. Cuando la
    modificacion tambien se perdio, ambas coinciden y el resultado es el mismo.

    Args:
        ruta: Archivo a inspeccionar.
        mtime: `st_mtime` ya conocido, para no repetir el `stat`.
        nacimiento: `st_birthtime` ya conocido. Si ambos vienen dados no se
            consulta el sistema de archivos en absoluto.
    """
    marcas: list[float] = []
    if mtime is not None:
        marcas.append(mtime)
    if nacimiento is not None and nacimiento > 0:
        marcas.append(nacimiento)

    if mtime is None or nacimiento is None:
        try:
            info = os.stat(ruta_del_sistema(ruta))
        except OSError:
            info = None
        if info is not None:
            if mtime is None:
                marcas.append(info.st_mtime)
            if nacimiento is None:
                # `st_birthtime` existe en Windows desde Python 3.12 y en BSD.
                crudo = getattr(info, "st_birthtime", None)
                if isinstance(crudo, (int, float)) and crudo > 0:
                    marcas.append(float(crudo))

    for marca in sorted(marcas):
        try:
            momento = _valida(datetime.fromtimestamp(marca))
        except (ValueError, OverflowError, OSError):
            continue
        if momento is not None:
            return momento
    return None


# --------------------------------------------------------------------------- #
# Cascada
# --------------------------------------------------------------------------- #

def continuar_cascada(
    ruta: Path,
    mtime: float | None,
    momento_metadatos: datetime | None,
    origen_metadatos: OrigenFecha,
    nacimiento: float | None = None,
) -> FechaMedia:
    """Completa la cascada partiendo de una fecha de metadatos ya leida.

    Permite que la fase de analisis, que abre la imagen una sola vez para
    obtener dimensiones y EXIF a la vez, reutilice esa lectura en lugar de
    volver a abrir el archivo.

    Args:
        ruta: Archivo, usado para el patron del nombre y el `mtime`.
        mtime: `st_mtime` ya conocido, para no repetir el `stat`.
        momento_metadatos: Fecha obtenida de EXIF o del contenedor, si hubo.
        origen_metadatos: Origen que corresponde a `momento_metadatos`.
        nacimiento: `st_birthtime` ya conocido, para no repetir el `stat`.

    Returns:
        Un `FechaMedia`; `valor` es `None` solo si todo fallo.
    """
    if momento_metadatos is not None:
        return FechaMedia(momento_metadatos, origen_metadatos)

    momento = fecha_desde_nombre(ruta.name)
    if momento is not None:
        return FechaMedia(momento, OrigenFecha.NOMBRE)

    momento = fecha_desde_sistema(ruta, mtime, nacimiento)
    if momento is not None:
        return FechaMedia(momento, OrigenFecha.SISTEMA)
    return _SIN_FECHA


def resolver_fecha(ruta: Path, tipo: TipoMedia, mtime: float | None = None) -> FechaMedia:
    """Aplica la cascada completa abriendo el archivo segun su tipo.

    Es la puerta de entrada comoda para uso como biblioteca. El pipeline
    interno usa `continuar_cascada` para no repetir aperturas.
    """
    if tipo.es_imagen:
        return continuar_cascada(ruta, mtime, fecha_desde_exif(ruta), OrigenFecha.EXIF)
    if tipo is TipoMedia.VIDEO:
        return continuar_cascada(
            ruta, mtime, fecha_desde_contenedor(ruta), OrigenFecha.CONTENEDOR
        )
    return continuar_cascada(ruta, mtime, None, OrigenFecha.DESCONOCIDO)


def etiqueta_anio(momento: datetime) -> str:
    """`2024`."""
    return f"{momento.year:04d}"


def etiqueta_mes(momento: datetime, con_nombre: bool = False) -> str:
    """`2024-05` o `2024-05-Mayo`."""
    base = f"{momento.year:04d}-{momento.month:02d}"
    return f"{base}-{_MESES_ES[momento.month - 1]}" if con_nombre else base
