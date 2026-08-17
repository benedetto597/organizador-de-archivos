"""Huellas digitales de archivos e imagenes.

Dos niveles de identidad:

* **Bytes**: BLAKE2b del archivo completo. Detecta copias byte a byte.
  Es la unica huella valida para video.
* **Pixeles**: BLAKE2b sobre los pixeles decodificados de cada cuadro.
  Detecta copias identicas en imagen cuyos bytes difieren (EXIF distinto,
  metadatos agregados por una app, recompresion sin perdida).

Las funciones de este modulo son deliberadamente de nivel superior del modulo
y reciben/devuelven tipos simples: se ejecutan en procesos hijos y deben ser
serializables por `pickle` bajo el modelo `spawn` de Windows.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from typing import Final

from PIL import Image, ImageSequence, UnidentifiedImageError

__all__ = [
    "ERRORES_IMAGEN",
    "FirmaImagen",
    "ResultadoHuella",
    "huella_de_bytes",
    "huella_de_pixeles",
    "huella_rapida_jpeg",
    "leer_firma_imagen",
    "TAMANO_DIGESTO",
]

#: 16 bytes = 128 bits. Probabilidad de colision despreciable para 10^6
#: archivos y la mitad del costo de memoria de un digesto de 32 bytes.
TAMANO_DIGESTO: Final = 16

#: Lectura en bloques de 1 MiB: buen equilibrio entre syscalls y cache.
_BLOQUE: Final = 1 << 20

#: Tope de cuadros a considerar en imagenes animadas.
_MAX_CUADROS: Final = 64

#: Pillow lanza mas cosas de las que su documentacion sugiere ante cabeceras
#: corruptas: `TypeError` e `IndexError` desde los plugins, `struct.error` al
#: desempaquetar campos truncados y `EOFError` al buscar un cuadro inexistente.
#: Cualquier omision aqui se convierte en una corrida abortada a mitad.
ERRORES_IMAGEN: Final = (
    OSError,
    ValueError,
    TypeError,
    IndexError,
    KeyError,
    EOFError,
    UnidentifiedImageError,
    SyntaxError,
    MemoryError,
    struct.error,
    Image.DecompressionBombError,
)

#: Formatos donde varios cuadros significan animacion. Un TIFF multipagina
#: (documento escaneado) o un MPO (par estereo de camara) tambien tienen varios
#: cuadros y no son animaciones: pertenecen al acervo fotografico.
_FORMATOS_ANIMADOS: Final = frozenset({"GIF", "WEBP", "PNG", "APNG", "MNG", "FLI", "FLC"})


@dataclass(frozen=True, slots=True)
class FirmaImagen:
    """Datos de cabecera que dos imagenes identicas comparten obligatoriamente.

    Sirve como prefiltro: si el ancho, alto, modo de color o numero de cuadros
    difieren, las imagenes no pueden ser identicas pixel a pixel y no hace
    falta decodificarlas.
    """

    ancho: int
    alto: int
    modo: str
    cuadros: int
    formato: str = ""

    @classmethod
    def desde_imagen(cls, imagen: Image.Image) -> FirmaImagen:
        """Construye la firma de una imagen ya abierta, sin decodificarla."""
        return cls(
            ancho=imagen.width,
            alto=imagen.height,
            modo=imagen.mode,
            cuadros=int(getattr(imagen, "n_frames", 1) or 1),
            formato=(imagen.format or ""),
        )

    @property
    def clave(self) -> tuple[int, int, str, int]:
        """Clave de agrupacion para el prefiltro.

        El formato queda fuera a proposito: un PNG y un JPEG pueden tener
        exactamente los mismos pixeles y son duplicados legitimos.
        """
        return (self.ancho, self.alto, self.modo, self.cuadros)

    @property
    def animada(self) -> bool:
        """True solo si el formato admite animacion y trae varios cuadros."""
        return self.cuadros > 1 and self.formato.upper() in _FORMATOS_ANIMADOS


@dataclass(frozen=True, slots=True)
class ResultadoHuella:
    """Resultado de un trabajador de hashing."""

    ruta: str
    huella: str | None
    error: str | None = None


def huella_de_bytes(ruta: str) -> ResultadoHuella:
    """Calcula BLAKE2b del contenido completo del archivo.

    Args:
        ruta: Ruta ya lista para el SO (con prefijo de ruta larga si aplica).

    Returns:
        `ResultadoHuella` con el digesto hexadecimal, o con `error` poblado.
    """
    resumen = hashlib.blake2b(digest_size=TAMANO_DIGESTO)
    try:
        with open(ruta, "rb", buffering=0) as manejador:
            while bloque := manejador.read(_BLOQUE):
                resumen.update(bloque)
    except OSError as exc:
        return ResultadoHuella(ruta, None, f"{type(exc).__name__}: {exc}")
    return ResultadoHuella(ruta, resumen.hexdigest())


def leer_firma_imagen(ruta: str) -> FirmaImagen | None:
    """Lee ancho, alto, modo y cuadros sin decodificar los pixeles.

    Pillow trabaja en modo perezoso: abrir la imagen solo lee la cabecera.
    Devuelve `None` si el archivo no es una imagen legible.
    """
    try:
        with Image.open(ruta) as imagen:
            return FirmaImagen.desde_imagen(imagen)
    except ERRORES_IMAGEN:
        return None


def huella_de_pixeles(ruta: str) -> ResultadoHuella:
    """Calcula BLAKE2b sobre los pixeles decodificados.

    El preimagen incluye modo y dimensiones de cada cuadro, de modo que dos
    imagenes con el mismo buffer crudo pero distinta interpretacion de color
    no colisionan.
    """
    resumen = hashlib.blake2b(digest_size=TAMANO_DIGESTO)
    try:
        with Image.open(ruta) as imagen:
            for indice, cuadro in enumerate(ImageSequence.Iterator(imagen)):
                if indice >= _MAX_CUADROS:
                    break
                resumen.update(f"{indice}|{cuadro.mode}|{cuadro.width}x{cuadro.height}|".encode())
                resumen.update(cuadro.tobytes())
    except ERRORES_IMAGEN as exc:
        return ResultadoHuella(ruta, None, f"{type(exc).__name__}: {exc}")
    return ResultadoHuella(ruta, resumen.hexdigest())


def huella_rapida_jpeg(ruta: str) -> ResultadoHuella:
    """Huella aproximada usando el escalado DCT de JPEG (modo `draft`).

    Decodifica a 1/8 de resolucion, lo que es cerca de 40 veces mas rapido que
    la decodificacion completa. Se usa solo como prefiltro opcional.

    Advertencia: dos imagenes con pixeles finales identicos pero producidas por
    codificadores distintos pueden dar huellas rapidas distintas. Por eso el
    prefiltro esta desactivado por defecto y solo descarta candidatos, nunca
    confirma un duplicado por si mismo.
    """
    resumen = hashlib.blake2b(digest_size=TAMANO_DIGESTO)
    try:
        with Image.open(ruta) as imagen:
            imagen.draft(imagen.mode, (max(1, imagen.width // 8), max(1, imagen.height // 8)))
            imagen.load()
            resumen.update(f"{imagen.mode}|{imagen.width}x{imagen.height}|".encode())
            resumen.update(imagen.tobytes())
    except ERRORES_IMAGEN as exc:
        return ResultadoHuella(ruta, None, f"{type(exc).__name__}: {exc}")
    return ResultadoHuella(ruta, resumen.hexdigest())
