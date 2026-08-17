"""Fase de analisis: una sola apertura por archivo para obtener todo.

Esta es la fase que paga el costo de disco, y por eso corre en paralelo.
En el acervo que motivo esta herramienta (HDD por USB) el **primer** acceso a
cada archivo cuesta unos 100 ms de busqueda de cabeza, mientras que los
accesos siguientes al mismo archivo cuestan 0,1 ms porque el sistema ya lo
tiene en cache. La consecuencia de diseno es directa:

* El escaneo no abre archivos: solo lee la entrada de directorio.
* Aqui, en paralelo, se abre cada archivo **una vez** y de esa unica apertura
  salen a la vez el tipo real, las dimensiones y la fecha EXIF.

Un registro `Analisis` es plano y serializable, para cruzar la frontera de
proceso sin sorpresas.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from PIL import Image

from .categorias import Categoria, clasificar_archivo
from .documentos import EXTENSIONES_CON_FECHA, fecha_desde_documento
from .escaneo import ArchivoEncontrado
from .fechas import (
    OrigenFecha,
    continuar_cascada,
    fecha_desde_contenedor,
    fecha_desde_imagen,
)
from .fuentes import clasificar_fuente
from .huellas import ERRORES_IMAGEN, FirmaImagen
from .rutas_seguras import ruta_del_sistema
from .tipos import TipoMedia, clasificar_con_cabecera, leer_cabecera

__all__ = ["Analisis", "analizar", "reconstruir_fecha"]

_log = logging.getLogger(__name__)

# La tupla de errores de Pillow vive en `huellas.ERRORES_IMAGEN`: es una sola
# fuente de verdad para todas las capas que abren imagenes, de modo que no
# puedan divergir con el tiempo y dejar una excepcion sin atrapar.


@dataclass(frozen=True, slots=True)
class Analisis:
    """Metadatos derivados de un archivo."""

    ruta: Path
    fecha_iso: str | None
    origen_fecha: str
    fuente: str
    firma: FirmaImagen | None
    tipo_efectivo: str
    #: Solo para archivos que no son audiovisuales. Vacio en fotos y videos,
    #: donde la carpeta de primer nivel la da `tipo_efectivo`.
    categoria: str = ""
    subtipo: str = ""

    @property
    def es_media(self) -> bool:
        """True si el archivo es foto, video o animacion."""
        return not self.categoria

    @property
    def tiene_fecha_confiable(self) -> bool:
        """True si la fecha no proviene del `mtime` del sistema de archivos."""
        return self.origen_fecha in (
            OrigenFecha.EXIF.value,
            OrigenFecha.CONTENEDOR.value,
            OrigenFecha.DOCUMENTO.value,
            OrigenFecha.NOMBRE.value,
        )

    @property
    def fecha(self) -> datetime | None:
        """Reconstruye el `datetime` a partir del ISO almacenado."""
        return reconstruir_fecha(self.fecha_iso)


def reconstruir_fecha(iso: str | None) -> datetime | None:
    """Convierte el ISO del analisis de vuelta a `datetime`."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:  # pragma: no cover - defensivo
        return None


@dataclass(frozen=True, slots=True)
class _LecturaImagen:
    """Lo que se obtiene de una unica apertura con Pillow."""

    firma: FirmaImagen
    fecha_exif: datetime | None


def _leer_imagen(ruta_os: str) -> _LecturaImagen | None:
    """Abre la imagen una vez y devuelve firma y fecha EXIF juntas.

    Devuelve `None` si Pillow no reconoce el archivo como imagen, lo que a su
    vez es la senal de que hay que caer al analisis por bytes magicos.
    """
    try:
        with Image.open(ruta_os) as imagen:
            return _LecturaImagen(
                FirmaImagen.desde_imagen(imagen), fecha_desde_imagen(imagen)
            )
    except ERRORES_IMAGEN:
        return None


def _tipo_por_firma_de_imagen(firma: FirmaImagen) -> TipoMedia:
    """Distingue animacion de foto.

    `firma.animada` exige que el formato admita animacion, no solo que haya
    varios cuadros: un TIFF multipagina o un MPO de camara tienen varios
    cuadros y pertenecen al acervo fotografico.
    """
    return TipoMedia.ANIMACION if firma.animada else TipoMedia.FOTO


def analizar(item: ArchivoEncontrado) -> Analisis:
    """Resuelve tipo real, fecha, fuente y firma de un archivo.

    Args:
        item: Archivo detectado por el escaneo, con el tipo tentativo que se
            deduce de su extension.

    Returns:
        Un `Analisis`. Nunca lanza: cualquier fallo degrada a la informacion
        disponible (tipo por extension, fecha del sistema, fuente por nombre).
        Esta garantia importa porque la funcion corre en procesos hijos sobre
        decenas de miles de archivos: una excepcion inesperada de un plugin de
        Pillow ante una cabecera corrupta se llevaria el lote entero.
    """
    try:
        return _analizar(item)
    except Exception as exc:  # noqa: BLE001 - red de seguridad deliberada
        _log.warning("Analisis degradado para %s: %r", item.ruta, exc)
        return _analisis_degradado(item)


def _analisis_degradado(item: ArchivoEncontrado) -> Analisis:
    """Analisis minimo que no abre el archivo. Solo usa nombre y `stat`."""
    fecha = continuar_cascada(
        item.ruta, item.mtime, None, OrigenFecha.DESCONOCIDO, item.nacimiento
    )
    categoria, subtipo = ("", "") if item.es_media else _categoria_de(item.ruta)
    return Analisis(
        ruta=item.ruta,
        fecha_iso=fecha.valor.isoformat(timespec="seconds") if fecha.valor else None,
        origen_fecha=fecha.origen.value,
        fuente=clasificar_fuente(item.nombre).value,
        firma=None,
        tipo_efectivo=item.tipo.value,
        categoria=categoria,
        subtipo=subtipo,
    )


def _analizar_archivo(item: ArchivoEncontrado) -> Analisis:
    """Analisis de un archivo que no es audiovisual.

    No se abre con Pillow. La fecha sale de los metadatos del documento cuando
    el formato los expone (OOXML, OpenDocument, PDF), y si no, del nombre o del
    sistema de archivos.

    Si la extension no esta en ninguna tabla se mira el contenido antes de
    rendirse. Esto es lo que hace util a `--incluir-ext`: un `.raf` o un `.insv`
    que en realidad es una imagen o un video acaba en Fotos o Videos en lugar de
    en `Otros-Archivos`, porque los bytes magicos lo delatan.
    """
    if clasificar_archivo(item.ruta) is None:
        tipo_real = clasificar_con_cabecera(item.ruta, leer_cabecera(item.ruta))
        if tipo_real is not TipoMedia.DESCONOCIDO:
            return _analizar_media(replace(item, tipo=tipo_real))

    categoria, subtipo = _categoria_de(item.ruta)
    momento = (
        fecha_desde_documento(item.ruta)
        if item.ruta.suffix.casefold() in EXTENSIONES_CON_FECHA
        else None
    )
    fecha = continuar_cascada(
        item.ruta, item.mtime, momento, OrigenFecha.DOCUMENTO, item.nacimiento
    )
    return Analisis(
        ruta=item.ruta,
        fecha_iso=fecha.valor.isoformat(timespec="seconds") if fecha.valor else None,
        origen_fecha=fecha.origen.value,
        # La "fuente" no se calcula para lo que no es audiovisual: sus patrones
        # (WhatsApp, Capturas, Camara) no aplican, y aplicarlos daba resultados
        # enganosos como clasificar `informe-01.pdf` en `Editadas`.
        fuente="",
        firma=None,
        tipo_efectivo=TipoMedia.DESCONOCIDO.value,
        categoria=categoria,
        subtipo=subtipo,
    )


def _categoria_de(ruta: Path) -> tuple[str, str]:
    """Categoria y subtipo de un archivo no audiovisual."""
    clasificado = clasificar_archivo(ruta)
    if clasificado is None:
        return Categoria.OTROS.value, _subtipo_por_extension(ruta)
    categoria, subtipo = clasificado
    return categoria.value, subtipo


def _subtipo_por_extension(ruta: Path) -> str:
    """Para lo no clasificado, agrupa por la extension en mayusculas.

    Deja `Otros-Archivos\\PDF-raro\\` en lugar de un cajon indiferenciado, de
    modo que al revisarlo se vea de un golpe que hay ahi.
    """
    extension = ruta.suffix.lstrip(".").upper()
    return extension[:12] if extension else "Sin-Extension"


def _analizar(item: ArchivoEncontrado) -> Analisis:
    """Cuerpo real del analisis. Ver `analizar` para el contrato publico."""
    if not item.es_media:
        return _analizar_archivo(item)
    return _analizar_media(item)


def _analizar_media(item: ArchivoEncontrado) -> Analisis:
    """Analisis de foto, video o animacion. Una sola apertura del archivo."""
    ruta_os = ruta_del_sistema(item.ruta)
    lectura = _leer_imagen(ruta_os) if item.tipo.es_imagen else None

    if lectura is not None:
        tipo = _tipo_por_firma_de_imagen(lectura.firma)
        fecha = continuar_cascada(
            item.ruta, item.mtime, lectura.fecha_exif, OrigenFecha.EXIF, item.nacimiento
        )
        firma = lectura.firma
    else:
        # No es una imagen legible: la extension mentia, o es video. Se usan
        # los bytes magicos, que ya estan en cache tras el intento anterior.
        tipo = clasificar_con_cabecera(item.ruta, leer_cabecera(item.ruta))
        firma = None
        if tipo.es_imagen:
            # Caso raro: extension de video con contenido de imagen.
            lectura = _leer_imagen(ruta_os)
            if lectura is not None:
                tipo = _tipo_por_firma_de_imagen(lectura.firma)
                firma = lectura.firma
            fecha = continuar_cascada(
                item.ruta,
                item.mtime,
                lectura.fecha_exif if lectura else None,
                OrigenFecha.EXIF,
                item.nacimiento,
            )
        elif tipo is TipoMedia.VIDEO:
            fecha = continuar_cascada(
                item.ruta,
                item.mtime,
                fecha_desde_contenedor(item.ruta),
                OrigenFecha.CONTENEDOR,
                item.nacimiento,
            )
        else:
            fecha = continuar_cascada(
                item.ruta, item.mtime, None, OrigenFecha.DESCONOCIDO, item.nacimiento
            )

    return Analisis(
        ruta=item.ruta,
        fecha_iso=fecha.valor.isoformat(timespec="seconds") if fecha.valor else None,
        origen_fecha=fecha.origen.value,
        fuente=clasificar_fuente(item.nombre).value,
        firma=firma,
        tipo_efectivo=tipo.value,
    )
