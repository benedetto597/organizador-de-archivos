"""Fecha de creacion desde los metadatos de un documento.

Es el equivalente del EXIF para lo que no es foto. Todo con biblioteca
estandar, sin dependencias nuevas:

* **OOXML** (`.docx`, `.xlsx`, `.pptx`): son archivos ZIP; la fecha esta en
  `docProps/core.xml`, en `dcterms:created`.
* **OpenDocument** (`.odt`, `.ods`, `.odp`): tambien ZIP; la fecha esta en
  `meta.xml`, en `dc:date` o `meta:creation-date`.
* **PDF**: se busca `/CreationDate (D:AAAAMMDD...)` en el texto del archivo.

Importa porque en un acervo de documentos el `mtime` suele ser la fecha en que
se copio el archivo al respaldo, no cuando se escribio. En el censo del disco
externo hay 2.714 PDF y 671 `.docx` que se benefician de esto.
"""

from __future__ import annotations

import logging
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Final

from .rutas_seguras import ruta_del_sistema

__all__ = [
    "fecha_desde_documento",
    "fecha_desde_ooxml",
    "fecha_desde_opendocument",
    "fecha_desde_pdf",
    "EXTENSIONES_CON_FECHA",
]

_log = logging.getLogger(__name__)

_OOXML: Final = frozenset({".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm"})
_OPENDOCUMENT: Final = frozenset({".odt", ".ods", ".odp", ".odg", ".odf"})
_PDF: Final = frozenset({".pdf"})

#: Extensiones para las que vale la pena intentar leer metadatos.
EXTENSIONES_CON_FECHA: Final = _OOXML | _OPENDOCUMENT | _PDF

#: Solo se leen los primeros y ultimos bloques del PDF: el diccionario de
#: informacion vive casi siempre en uno de los dos extremos, y asi un PDF de
#: 500 MB no se lee completo.
_BLOQUE_PDF: Final = 256 * 1024

#: Tope de tamano del XML de metadatos que se acepta de un ZIP, para que un
#: archivo manipulado no pueda inflar la memoria (bomba de descompresion).
_MAX_XML: Final = 1 << 20

_RE_CREATED_XML = re.compile(
    rb"<(?:dcterms:created|meta:creation-date|dc:date)[^>]*>([^<]{4,40})<", re.IGNORECASE
)
_RE_PDF_FECHA = re.compile(rb"/CreationDate\s*\(\s*D?:?\s*(\d{8}(?:\d{0,6})?)")

_ERRORES_ZIP = (zipfile.BadZipFile, KeyError, OSError, ValueError, RuntimeError, EOFError)


def _valida(momento: datetime | None) -> datetime | None:
    """Rechaza fechas imposibles, con el mismo criterio que `fechas.py`."""
    from .fechas import _valida as validar_comun  # import local: evita ciclo

    return validar_comun(momento)


def _parsear_iso(crudo: bytes | str) -> datetime | None:
    """Interpreta las formas ISO 8601 que usan OOXML y OpenDocument."""
    texto = crudo.decode("utf-8", "ignore") if isinstance(crudo, bytes) else crudo
    texto = texto.strip().replace("Z", "").split(".")[0]
    for formato in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return _valida(datetime.strptime(texto[:19], formato))
        except ValueError:
            continue
    return None


def _leer_del_zip(ruta: Path, candidatos: tuple[str, ...]) -> datetime | None:
    """Busca la fecha de creacion dentro de un contenedor ZIP."""
    try:
        with zipfile.ZipFile(ruta_del_sistema(ruta)) as paquete:
            for nombre in candidatos:
                try:
                    info = paquete.getinfo(nombre)
                except KeyError:
                    continue
                if info.file_size > _MAX_XML:
                    continue
                with paquete.open(info) as manejador:
                    crudo = manejador.read(_MAX_XML)
                coincidencia = _RE_CREATED_XML.search(crudo)
                if coincidencia:
                    momento = _parsear_iso(coincidencia.group(1))
                    if momento is not None:
                        return momento
    except _ERRORES_ZIP as exc:
        _log.debug("Metadatos ZIP ilegibles en %s: %s", ruta.name, exc)
    return None


def fecha_desde_ooxml(ruta: Path) -> datetime | None:
    """Fecha de creacion de un documento de Office moderno."""
    return _leer_del_zip(ruta, ("docProps/core.xml",))


def fecha_desde_opendocument(ruta: Path) -> datetime | None:
    """Fecha de creacion de un documento OpenDocument."""
    return _leer_del_zip(ruta, ("meta.xml",))


def fecha_desde_pdf(ruta: Path) -> datetime | None:
    """Fecha de creacion declarada en el diccionario de informacion del PDF.

    Solo lee los extremos del archivo. El formato de fecha del estandar es
    `D:AAAAMMDDHHmmSS`, con todo despues del ano opcional.
    """
    try:
        with open(ruta_del_sistema(ruta), "rb") as manejador:
            inicio = manejador.read(_BLOQUE_PDF)
            manejador.seek(0, 2)
            total = manejador.tell()
            if total > _BLOQUE_PDF:
                manejador.seek(max(0, total - _BLOQUE_PDF))
                final = manejador.read(_BLOQUE_PDF)
            else:
                final = b""
    except OSError as exc:
        _log.debug("PDF ilegible en %s: %s", ruta.name, exc)
        return None

    for bloque in (inicio, final):
        coincidencia = _RE_PDF_FECHA.search(bloque)
        if not coincidencia:
            continue
        crudo = coincidencia.group(1).decode("ascii", "ignore")
        relleno = (crudo + "00000101000000")[:14]
        try:
            momento = datetime(
                int(relleno[0:4]), int(relleno[4:6]), int(relleno[6:8]),
                int(relleno[8:10]), int(relleno[10:12]), int(relleno[12:14]),
            )
        except ValueError:
            continue
        validada = _valida(momento)
        if validada is not None:
            return validada
    return None


def fecha_desde_documento(ruta: Path) -> datetime | None:
    """Intenta leer la fecha de creacion segun la extension del documento.

    Args:
        ruta: Archivo a inspeccionar.

    Returns:
        La fecha declarada en los metadatos, o `None` si el formato no la
        expone, esta corrupta o el archivo no es de un tipo con metadatos.
        Nunca lanza.
    """
    extension = ruta.suffix.casefold()
    if extension in _OOXML:
        return fecha_desde_ooxml(ruta)
    if extension in _OPENDOCUMENT:
        return fecha_desde_opendocument(ruta)
    if extension in _PDF:
        return fecha_desde_pdf(ruta)
    return None
