"""Clasificacion del origen del archivo a partir de su nombre.

Las reglas son 100% deterministas y estan calibradas contra el acervo real
que se inspecciono (57.701 archivos): `IMG-*-WA*` de mensajeria, `Screenshot_*`
de capturas, `\\d{8}_\\d{6}` de camara Samsung, `FB_IMG_*` de redes y
`100XXXXXXX.jpg` de IDs numericos de galeria.

El orden de la tupla `_REGLAS` es la prioridad: la primera coincidencia gana.
Cambiar el orden cambia la clasificacion, asi que cada posicion esta razonada
en los comentarios.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Final

__all__ = ["Fuente", "clasificar_fuente", "reglas_activas"]


class Fuente(str, Enum):
    """Origen detectado. El valor es el nombre de la carpeta hoja."""

    CAPTURAS = "Capturas"
    WHATSAPP = "WhatsApp"
    REDES = "Redes-Sociales"
    EDITADAS = "Editadas"
    CAMARA = "Camara"
    GALERIA = "Galeria"
    DESCARGAS = "Descargas"
    OTROS = "Otros"

    @property
    def carpeta(self) -> str:
        """Nombre de carpeta asociado a la fuente."""
        return self.value

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


def _c(patron: str) -> re.Pattern[str]:
    """Compila sin distinguir mayusculas."""
    return re.compile(patron, re.IGNORECASE)


#: (fuente, patron). Orden = prioridad.
_REGLAS: Final[tuple[tuple[Fuente, re.Pattern[str]], ...]] = (
    # 1. Capturas primero: `Screenshot_20240608_193933_WhatsApp.jpg` es una
    #    captura de pantalla, no un archivo recibido por WhatsApp.
    (Fuente.CAPTURAS, _c(r"^(?:screenshot|screen[_-]?recording|screen[_-]?shot|scrnshot|captura)")),
    (Fuente.CAPTURAS, _c(r"^(?:grabacion|record)[_-]?(?:de[_-]?)?pantalla")),

    # 2. Mensajeria: el sufijo WA es inequivoco.
    (Fuente.WHATSAPP, _c(r"^(?:IMG|VID|AUD|PTT|DOC|STK)[-_]\d{8}[-_]WA\d+")),
    (Fuente.WHATSAPP, _c(r"^whatsapp[ _-](?:image|video|audio|animated)")),
    (Fuente.WHATSAPP, _c(r"[-_]WA\d{4}(?:\D|$)")),

    # 3. Redes sociales y apps de terceros con prefijo propio.
    (Fuente.REDES, _c(r"^(?:FB_IMG|FB_VID|fbdown|received[_-]|messenger|snapchat|"
                      r"instagram|insta[_-]|tiktok|twitter|tw[_-]img|telegram|tg[_-]image|"
                      r"viber|pinterest|linkedin)")),

    # 4. Ediciones y copias. Va antes de Camara porque `20231008_171110-01.jpg`
    #    es una foto de camara *editada*: el sufijo `-NN` lo agrega el editor.
    #    La extension queda abierta a proposito: la herramienta reconoce 48
    #    extensiones de media y fijar una lista blanca aqui las dejaria fuera.
    (Fuente.EDITADAS, _c(r"[-_]\d{2}\.[A-Za-z0-9]{2,5}$")),
    (Fuente.EDITADAS, _c(r"(?:remini|picsart|snapseed|lightroom|photoshop|inshot|"
                         r"canva|collage|^penup|meitu|prisma|facetune|vsco|capcut)")),
    # `~\d+` NO aparece aqui a proposito: ese sufijo lo genera
    # `rutas_seguras.nombre_disponible` al resolver colisiones, y reclamarlo
    # cambiaria la fuente de un archivo que la propia herramienta renombro,
    # rompiendo la idempotencia de `--reprocesar`.
    (Fuente.EDITADAS, _c(r"(?:[-_]edit(?:ed|ada)?|[-_]crop|[-_]recorte|[-_]resize[dn]?|"
                         r"[-_]compress|[-_]copia|[-_]copy|\(\d+\))\.")),

    # 5. Camara del dispositivo.
    (Fuente.CAMARA, _c(r"^(?:IMG|VID|PXL|PANO|BURST|MVIMG|TRIM|DSC|DSCN|GOPR|SAM|"
                       r"P_?)[-_]?\d{4,8}")),
    (Fuente.CAMARA, _c(r"^\d{8}[_-]\d{6}")),
    (Fuente.CAMARA, _c(r"^(?:100|101|102)[_-]\d{4}")),
    (Fuente.CAMARA, _c(r"^(?:selfie|camera|camara|foto)[-_]?\d")),

    # 6. IDs numericos de galeria / mensajeria (`1000035167.jpg`): 18.275
    #    archivos del acervo. No traen fecha ni en nombre ni en EXIF.
    (Fuente.GALERIA, _c(r"^\d{9,12}\.\w+$")),
    (Fuente.GALERIA, _c(r"^(?:gallery|galeria)[-_]?\d")),

    # 7. Descargas y contenido de apps: UUID, hashes, nombres genericos.
    (Fuente.DESCARGAS, _c(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")),
    (Fuente.DESCARGAS, _c(r"^[0-9a-f]{24,}")),
    (Fuente.DESCARGAS, _c(r"^(?:download|descarga|unnamed|untitled|sin[-_]titulo|"
                          r"image[-_]?\d+|imagen[-_]?\d+|file[-_]?\d+|tmp|temp)")),
    (Fuente.DESCARGAS, _c(r"^(?:doc|documento)[-_]\d")),
)


def clasificar_fuente(nombre: str) -> Fuente:
    """Determina el origen de un archivo por su nombre.

    Args:
        nombre: Nombre del archivo con extension.

    Returns:
        La primera `Fuente` cuyo patron coincide, o `Fuente.OTROS`.

    Examples:
        >>> clasificar_fuente('IMG-20190606-WA0044.jpg').value
        'WhatsApp'
        >>> clasificar_fuente('Screenshot_20240608_193933_WhatsApp.jpg').value
        'Capturas'
        >>> clasificar_fuente('FB_IMG_1561306096025.jpg').value
        'Redes-Sociales'
        >>> clasificar_fuente('20230726_124814.jpg').value
        'Camara'
        >>> clasificar_fuente('20231008_171110-01.jpeg').value
        'Editadas'
        >>> clasificar_fuente('1000035167.jpg').value
        'Galeria'
        >>> clasificar_fuente('vacaciones.jpg').value
        'Otros'
    """
    for fuente, patron in _REGLAS:
        if patron.search(nombre):
            return fuente
    return Fuente.OTROS


def reglas_activas() -> tuple[tuple[str, str], ...]:
    """Devuelve las reglas como texto. Util para documentar o depurar."""
    return tuple((fuente.value, patron.pattern) for fuente, patron in _REGLAS)
