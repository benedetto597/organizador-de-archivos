"""Utilidades compartidas por las pruebas: arboles temporales y archivos falsos."""

from __future__ import annotations

import struct
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo

__all__ = [
    "crear_imagen",
    "crear_imagen_con_metadatos",
    "crear_jpeg_con_exif",
    "crear_mp4",
    "crear_archivo",
    "EPOCA_BMFF",
]

EPOCA_BMFF = datetime(1904, 1, 1, tzinfo=timezone.utc)


def crear_imagen(ruta: Path, color: tuple[int, int, int] = (10, 20, 30), tamano=(8, 6)) -> Path:
    """Escribe un PNG solido y determinista."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", tamano, color).save(ruta, format="PNG")
    return ruta


def crear_imagen_con_metadatos(
    ruta: Path, color: tuple[int, int, int] = (10, 20, 30), tamano=(8, 6), nota: str = "x"
) -> Path:
    """PNG con los mismos pixeles que `crear_imagen` pero bytes distintos.

    Sirve para probar que la deteccion a nivel de pixel encuentra copias que
    la comparacion de bytes no puede encontrar.
    """
    ruta.parent.mkdir(parents=True, exist_ok=True)
    info = PngInfo()
    info.add_text("Comentario", nota * 40)
    Image.new("RGB", tamano, color).save(ruta, format="PNG", pnginfo=info)
    return ruta


def crear_jpeg_con_exif(ruta: Path, momento: datetime, tamano=(16, 12)) -> Path:
    """JPEG con `DateTimeOriginal` poblado."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    imagen = Image.new("RGB", tamano, (200, 100, 50))
    exif = imagen.getexif()
    exif[0x0132] = momento.strftime("%Y:%m:%d %H:%M:%S")
    sub = exif.get_ifd(0x8769)
    sub[0x9003] = momento.strftime("%Y:%m:%d %H:%M:%S")
    imagen.save(ruta, format="JPEG", exif=exif)
    return ruta


def _caja(tipo: bytes, cuerpo: bytes) -> bytes:
    """Serializa una caja ISO-BMFF con su tamano de 32 bits."""
    return struct.pack(">I", len(cuerpo) + 8) + tipo + cuerpo


def crear_mp4(ruta: Path, creacion: datetime | None, relleno: int = 4096) -> Path:
    """MP4 minimo valido con `moov` al FINAL del archivo.

    La posicion de `moov` es deliberada: es el caso que rompe cualquier
    implementacion que busque la cadena `mvhd` en los primeros megabytes.
    """
    ruta.parent.mkdir(parents=True, exist_ok=True)
    segundos = 0
    if creacion is not None:
        momento = creacion.replace(tzinfo=timezone.utc)
        segundos = int((momento - EPOCA_BMFF).total_seconds())

    mvhd_cuerpo = (
        bytes([0])                      # version 0
        + b"\x00\x00\x00"               # flags
        + struct.pack(">I", segundos)   # creation_time
        + struct.pack(">I", segundos)   # modification_time
        + struct.pack(">I", 1000)       # timescale
        + struct.pack(">I", 5000)       # duration
        + b"\x00" * 80                  # matriz y reservados
    )
    ftyp = _caja(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2avc1mp41")
    mdat = _caja(b"mdat", b"\x00" * relleno)
    moov = _caja(b"moov", _caja(b"mvhd", mvhd_cuerpo))
    ruta.write_bytes(ftyp + mdat + moov)
    return ruta


def crear_archivo(ruta: Path, contenido: bytes) -> Path:
    """Escribe bytes crudos creando la carpeta si hace falta."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(contenido)
    return ruta
