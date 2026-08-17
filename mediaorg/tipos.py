"""Clasificacion del tipo de medio: extension primero, bytes magicos despues.

No se confia en la extension como unica fuente de verdad: un `.jpg` que en
realidad es un MP4 (caso comun en respaldos de mensajeria) debe terminar en
Videos. La firma binaria manda cuando contradice a la extension.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .rutas_seguras import ruta_del_sistema

__all__ = [
    "TipoMedia",
    "EXTENSIONES_FOTO",
    "EXTENSIONES_VIDEO",
    "EXTENSIONES_ANIMACION",
    "EXTENSIONES_CONOCIDAS",
    "leer_cabecera",
    "tipo_por_extension",
    "tipo_por_firma",
    "clasificar",
    "clasificar_con_cabecera",
    "TAMANO_CABECERA",
]

#: Bytes que se leen del inicio del archivo para identificar la firma.
TAMANO_CABECERA = 32

EXTENSIONES_FOTO = frozenset(
    {
        ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp", ".bmp", ".tif", ".tiff",
        ".heic", ".heif", ".avif", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf",
        ".raf", ".rw2", ".ico", ".jp2", ".pgm", ".ppm", ".tga",
    }
)

EXTENSIONES_VIDEO = frozenset(
    {
        ".mp4", ".m4v", ".mov", ".3gp", ".3g2", ".avi", ".mkv", ".webm", ".wmv",
        ".flv", ".mpg", ".mpeg", ".mts", ".m2ts", ".vob", ".ogv", ".asf",
        ".mpe", ".divx", ".f4v",
        # `.ts` NO esta aqui a proposito: en la practica es TypeScript mucho mas
        # a menudo que un flujo de transporte MPEG, y clasificar codigo como
        # video lo mandaria a la carpeta equivocada. El video de camara con esa
        # familia de contenedores usa `.mts` y `.m2ts`, que si estan cubiertos.
    }
)

EXTENSIONES_ANIMACION = frozenset({".gif", ".apng"})

EXTENSIONES_CONOCIDAS = EXTENSIONES_FOTO | EXTENSIONES_VIDEO | EXTENSIONES_ANIMACION


class TipoMedia(str, Enum):
    """Categoria de primer nivel del arbol de salida."""

    FOTO = "foto"
    VIDEO = "video"
    ANIMACION = "animacion"
    DESCONOCIDO = "desconocido"

    @property
    def carpeta(self) -> str:
        """Nombre de la carpeta raiz asociada al tipo."""
        return _CARPETAS[self]

    @property
    def es_imagen(self) -> bool:
        """True para lo que Pillow puede decodificar como imagen."""
        return self in (TipoMedia.FOTO, TipoMedia.ANIMACION)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


_CARPETAS: dict[TipoMedia, str] = {
    TipoMedia.FOTO: "Fotos",
    TipoMedia.VIDEO: "Videos",
    TipoMedia.ANIMACION: "Animaciones",
    TipoMedia.DESCONOCIDO: "Otros-Archivos",
}


@dataclass(frozen=True, slots=True)
class _Firma:
    """Patron de bytes en un desplazamiento fijo."""

    desplazamiento: int
    patron: bytes
    tipo: TipoMedia

    def coincide(self, cabecera: bytes) -> bool:
        fin = self.desplazamiento + len(self.patron)
        return len(cabecera) >= fin and cabecera[self.desplazamiento : fin] == self.patron


#: Firmas sin ambiguedad. El orden no importa: los patrones son disjuntos.
_FIRMAS: tuple[_Firma, ...] = (
    _Firma(0, b"\xff\xd8\xff", TipoMedia.FOTO),                 # JPEG
    _Firma(0, b"\x89PNG\r\n\x1a\n", TipoMedia.FOTO),            # PNG
    _Firma(0, b"BM", TipoMedia.FOTO),                           # BMP
    _Firma(0, b"II*\x00", TipoMedia.FOTO),                      # TIFF LE / RAW
    _Firma(0, b"MM\x00*", TipoMedia.FOTO),                      # TIFF BE
    _Firma(0, b"GIF87a", TipoMedia.ANIMACION),                   # GIF
    _Firma(0, b"GIF89a", TipoMedia.ANIMACION),
    _Firma(0, b"\x1a\x45\xdf\xa3", TipoMedia.VIDEO),            # Matroska / WebM
    _Firma(0, b"OggS", TipoMedia.VIDEO),                         # Ogg
    _Firma(0, b"FLV\x01", TipoMedia.VIDEO),                      # FLV
    _Firma(0, b"\x30\x26\xb2\x75", TipoMedia.VIDEO),            # ASF / WMV
    _Firma(0, b"\x00\x00\x01\xba", TipoMedia.VIDEO),            # MPEG-PS
    _Firma(0, b"\x00\x00\x01\xb3", TipoMedia.VIDEO),            # MPEG video
)

#: Marcas de la familia ISO-BMFF (`ftyp`) que en realidad son imagenes.
_MARCAS_BMFF_IMAGEN = frozenset(
    {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1", b"avif", b"avis"}
)


def leer_cabecera(ruta: Path, tamano: int = TAMANO_CABECERA) -> bytes:
    """Lee los primeros bytes del archivo. Devuelve `b""` si no se puede."""
    try:
        with open(ruta_del_sistema(ruta), "rb") as manejador:
            return manejador.read(tamano)
    except OSError:
        return b""


def tipo_por_extension(ruta: Path, extra: frozenset[str] = frozenset()) -> TipoMedia:
    """Clasifica solo por extension, sin tocar el contenido."""
    extension = ruta.suffix.casefold()
    if extension in EXTENSIONES_FOTO:
        return TipoMedia.FOTO
    if extension in EXTENSIONES_VIDEO:
        return TipoMedia.VIDEO
    if extension in EXTENSIONES_ANIMACION:
        return TipoMedia.ANIMACION
    if extension in extra:
        return TipoMedia.DESCONOCIDO
    return TipoMedia.DESCONOCIDO


def tipo_por_firma(cabecera: bytes) -> TipoMedia | None:
    """Clasifica por bytes magicos. `None` si la firma no se reconoce."""
    if len(cabecera) < 4:
        return None
    for firma in _FIRMAS:
        if firma.coincide(cabecera):
            return firma.tipo
    if len(cabecera) >= 12:
        if cabecera[4:8] == b"ftyp":
            marca = cabecera[8:12].lower()  # bytes no tiene casefold()
            return TipoMedia.FOTO if marca in _MARCAS_BMFF_IMAGEN else TipoMedia.VIDEO
        if cabecera[0:4] == b"RIFF":
            etiqueta = cabecera[8:12]
            if etiqueta == b"WEBP":
                return TipoMedia.FOTO
            if etiqueta == b"AVI ":
                return TipoMedia.VIDEO
    return None


def clasificar(ruta: Path, extra: frozenset[str] = frozenset()) -> TipoMedia:
    """Determina el tipo abriendo el archivo para leer su firma."""
    return clasificar_con_cabecera(ruta, leer_cabecera(ruta), extra)


def clasificar_con_cabecera(
    ruta: Path, cabecera: bytes, extra: frozenset[str] = frozenset()
) -> TipoMedia:
    """Determina el tipo combinando extension y una cabecera ya leida.

    La firma tiene la ultima palabra cuando reconoce el formato. Si la firma
    es desconocida se respeta la extension; si ninguna de las dos aporta
    informacion se devuelve `DESCONOCIDO`.

    Recibe la cabecera en lugar de leerla para que la lectura ocurra en la
    fase paralela del pipeline: en un disco USB el primer acceso a cada
    archivo cuesta ~100 ms y hacerlo en serie domina el tiempo total.
    """
    por_extension = tipo_por_extension(ruta, extra)
    por_firma = tipo_por_firma(cabecera)
    if por_firma is None:
        return por_extension
    if por_firma is TipoMedia.ANIMACION and por_extension is TipoMedia.FOTO:
        # APNG/WebP animado se resuelve luego con el conteo real de cuadros.
        return TipoMedia.FOTO
    return por_firma
