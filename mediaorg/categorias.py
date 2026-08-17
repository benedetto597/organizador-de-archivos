"""Taxonomia de archivos que no son foto ni video.

La tabla esta calibrada contra el censo real del disco externo que motivo esta
extension (75.481 archivos): 2.714 PDF, 1.284 `.py`, 1.200 `.jar`, 855 `.xml`,
429 `.sql`, 217 `.po`, 190 `.zip`, 185 `.srt`, 139 `.msi`, 120 `.skp`,
93 `.pkt` de Packet Tracer, 51 `.nsp` de Switch. Cada categoria de esta lista
existe porque hay archivos reales que caen en ella, no por completitud teorica.

Convive con `tipos.py`, que sigue siendo la autoridad para lo audiovisual: las
dos tablas son disjuntas y hay una prueba que lo verifica.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

__all__ = [
    "Categoria",
    "clasificar_archivo",
    "extensiones_registradas",
    "SUBTIPO_DESCONOCIDO",
]

#: Subtipo usado cuando la categoria no distingue variantes.
SUBTIPO_DESCONOCIDO = "Otros"


class Categoria(str, Enum):
    """Carpeta de primer nivel para archivos que no son foto ni video."""

    DOCUMENTOS = "Documentos"
    COMPRIMIDOS = "Comprimidos"
    INSTALADORES = "Instaladores"
    AUDIO = "Audio"
    CODIGO = "Codigo"
    DISENO = "Diseno"
    DATOS = "Bases-de-datos"
    CORREO = "Correo"
    LLAVES = "Certificados-y-llaves"
    VIRTUALIZACION = "Maquinas-virtuales"
    REDES = "Redes"
    JUEGOS = "Juegos"
    SUBTITULOS = "Subtitulos"
    FUENTES = "Fuentes"
    ATAJOS = "Atajos"
    TORRENTS = "Torrents"
    OTROS = "Otros-Archivos"

    @property
    def carpeta(self) -> str:
        """Nombre de la carpeta asociada."""
        return self.value

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


#: `extension -> (categoria, subtipo)`. Se construye con `_reg` para que la
#: fuente sea legible y no un diccionario literal gigante.
_TABLA: dict[str, tuple[Categoria, str]] = {}


def _reg(categoria: Categoria, subtipo: str, *extensiones: str) -> None:
    """Registra extensiones en la tabla, detectando duplicados al importar."""
    for extension in extensiones:
        clave = extension if extension.startswith(".") else f".{extension}"
        clave = clave.casefold()
        if clave in _TABLA:  # pragma: no cover - error de programacion
            raise ValueError(f"Extension duplicada en la taxonomia: {clave}")
        _TABLA[clave] = (categoria, subtipo)


# --------------------------------------------------------------------------- #
# Documentos
# --------------------------------------------------------------------------- #
_reg(Categoria.DOCUMENTOS, "PDF", ".pdf")
_reg(Categoria.DOCUMENTOS, "Word", ".doc", ".docx", ".docm", ".dot", ".dotx", ".odt", ".rtf", ".pages", ".wpd")
_reg(Categoria.DOCUMENTOS, "Hojas-de-calculo", ".xls", ".xlsx", ".xlsm", ".xlsb", ".ods", ".numbers", ".csv", ".tsv")
# `.key` no se registra como Keynote: en un disco con carpeta de criptografia
# es mucho mas probable que sea una llave privada, y confundirlas seria grave.
_reg(Categoria.DOCUMENTOS, "Presentaciones", ".ppt", ".pptx", ".pptm", ".pps", ".ppsx", ".odp")
_reg(Categoria.DOCUMENTOS, "Texto", ".txt", ".md", ".rst", ".log", ".nfo", ".tex", ".adoc")
_reg(Categoria.DOCUMENTOS, "Ebooks", ".epub", ".mobi", ".azw", ".azw3", ".fb2", ".djvu", ".cbz", ".cbr", ".lit")
# `.mm` se deja para Objective-C++ en la categoria de codigo, que es su uso
# dominante; los mapas mentales quedan cubiertos por `.xmind` y `.mmap`.
_reg(Categoria.DOCUMENTOS, "Notas", ".one", ".onetoc2", ".xmind", ".mmap", ".enex")
_reg(Categoria.DOCUMENTOS, "Diagramas", ".drawio", ".vsd", ".vsdx", ".graffle")
#: Paginas guardadas desde el navegador. Aparecieron en el disco real.
_reg(Categoria.DOCUMENTOS, "Paginas-guardadas", ".mhtml", ".mht", ".maff", ".webarchive")

# --------------------------------------------------------------------------- #
# Comprimidos
# --------------------------------------------------------------------------- #
_reg(Categoria.COMPRIMIDOS, "ZIP", ".zip", ".zipx", ".jar", ".war", ".ear")
_reg(Categoria.COMPRIMIDOS, "RAR", ".rar", ".r00", ".r01")
_reg(Categoria.COMPRIMIDOS, "7z", ".7z")
_reg(Categoria.COMPRIMIDOS, "TAR", ".tar", ".gz", ".tgz", ".bz2", ".tbz", ".xz", ".txz", ".zst", ".lz", ".lzma")
_reg(Categoria.COMPRIMIDOS, "Comprimidos-de-imagen", ".mdz", ".uha")
_reg(Categoria.COMPRIMIDOS, "Otros", ".cab", ".arj", ".lzh", ".ace", ".sit", ".sitx", ".z")

# --------------------------------------------------------------------------- #
# Instaladores
# --------------------------------------------------------------------------- #
_reg(Categoria.INSTALADORES, "Windows", ".exe", ".msi", ".msix", ".msixbundle", ".appx", ".appxbundle", ".msp", ".msu")
_reg(Categoria.INSTALADORES, "Imagenes-de-disco", ".iso", ".img", ".bin", ".cue", ".mdf", ".nrg", ".dmg", ".toast")
_reg(Categoria.INSTALADORES, "Android", ".apk", ".xapk", ".apks", ".aab", ".obb")
_reg(Categoria.INSTALADORES, "Linux", ".deb", ".rpm", ".appimage", ".snap", ".flatpak", ".pkg")
_reg(Categoria.INSTALADORES, "Scripts", ".bat", ".cmd", ".ps1", ".sh", ".vbs", ".reg", ".ahk")

# --------------------------------------------------------------------------- #
# Audio
# --------------------------------------------------------------------------- #
_reg(Categoria.AUDIO, "Comprimido", ".mp3", ".aac", ".m4a", ".ogg", ".oga", ".opus", ".wma", ".ra")
_reg(Categoria.AUDIO, "Sin-Perdida", ".flac", ".wav", ".aiff", ".aif", ".alac", ".ape", ".wv")
_reg(Categoria.AUDIO, "Voz", ".amr", ".3ga", ".awb", ".spx", ".m4b", ".aa", ".aax")
_reg(Categoria.AUDIO, "Partituras-y-midi", ".mid", ".midi", ".kar", ".mscz", ".gp5", ".gpx")

# --------------------------------------------------------------------------- #
# Codigo
# --------------------------------------------------------------------------- #
_reg(Categoria.CODIGO, "Python", ".py", ".pyw", ".pyi", ".pyx", ".pyc", ".pyo")
_reg(Categoria.CODIGO, "Notebooks", ".ipynb", ".rmd", ".qmd")
_reg(Categoria.CODIGO, "Java", ".java", ".class", ".kt", ".kts", ".scala", ".groovy")
_reg(Categoria.CODIGO, "JavaScript", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte")
_reg(Categoria.CODIGO, "Web", ".html", ".htm", ".xhtml", ".css", ".scss", ".sass", ".less", ".php", ".asp", ".aspx", ".jsp")
_reg(Categoria.CODIGO, "C-y-similares", ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".cs", ".m", ".mm", ".rs", ".go", ".swift", ".zig")
_reg(Categoria.CODIGO, "Otros-lenguajes", ".rb", ".pl", ".lua", ".r", ".jl", ".dart", ".ex", ".exs", ".erl", ".hs", ".clj", ".vb", ".pas", ".f90", ".asm")
_reg(Categoria.CODIGO, "Datos-estructurados", ".json", ".jsonl", ".xml", ".yaml", ".yml", ".toml", ".plist", ".proto", ".graphql")
_reg(Categoria.CODIGO, "Configuracion", ".ini", ".cfg", ".conf", ".properties", ".env", ".prefs", ".editorconfig", ".launch")
_reg(Categoria.CODIGO, "Traducciones", ".po", ".pot", ".mo", ".xliff", ".resx", ".strings")
_reg(Categoria.CODIGO, "Parches", ".patch", ".diff")

# --------------------------------------------------------------------------- #
# Diseno y modelado
# --------------------------------------------------------------------------- #
_reg(Categoria.DISENO, "Vector", ".svg", ".ai", ".eps", ".cdr", ".afdesign", ".sketch", ".fig", ".xd")
_reg(Categoria.DISENO, "Raster-editable", ".psd", ".psb", ".xcf", ".afphoto", ".kra", ".clip")
# `.pub` se reserva para llaves publicas SSH, no para Microsoft Publisher.
_reg(Categoria.DISENO, "Maquetacion", ".indd", ".idml", ".cdt", ".qxd")
# `.3ds` queda como escena de 3ds Max; las ROM de Nintendo 3DS usan `.cia`.
_reg(Categoria.DISENO, "3D", ".skp", ".blend", ".stl", ".obj", ".fbx", ".dae", ".3ds", ".max", ".c4d", ".gltf", ".glb", ".step", ".stp")
_reg(Categoria.DISENO, "CAD", ".dwg", ".dxf", ".rvt", ".rfa", ".ifc", ".sldprt", ".sldasm", ".catpart")

# --------------------------------------------------------------------------- #
# Datos y bases de datos
# --------------------------------------------------------------------------- #
_reg(Categoria.DATOS, "Scripts-SQL", ".sql", ".ddl", ".dml")
_reg(Categoria.DATOS, "Bases-locales", ".db", ".sqlite", ".sqlite3", ".db3", ".mdb", ".accdb", ".dbf", ".realm")
_reg(Categoria.DATOS, "Respaldos", ".bak", ".dump", ".bacpac", ".mdf_bak", ".trn")
# `.sav` se deja para partidas guardadas, no para SPSS: en este disco hay una
# carpeta de juegos y ninguna de estadistica.
_reg(Categoria.DATOS, "Cientificos", ".npy", ".npz", ".parquet", ".feather", ".h5", ".hdf5", ".mat", ".dta", ".arff", ".pkl", ".pickle")
_reg(Categoria.DATOS, "ETL", ".ktr", ".kjb", ".dtsx", ".pbix", ".pbit", ".twb", ".twbx")

# --------------------------------------------------------------------------- #
# Otras categorias
# --------------------------------------------------------------------------- #
_reg(Categoria.CORREO, SUBTIPO_DESCONOCIDO, ".mbox", ".eml", ".msg", ".pst", ".ost", ".olm", ".emlx")
_reg(Categoria.LLAVES, SUBTIPO_DESCONOCIDO, ".pem", ".key", ".crt", ".cer", ".der", ".pfx", ".p12", ".jks", ".keystore", ".asc", ".gpg", ".pgp", ".kdbx", ".ppk", ".pub")
_reg(Categoria.VIRTUALIZACION, SUBTIPO_DESCONOCIDO, ".ova", ".ovf", ".vmdk", ".vdi", ".vhd", ".vhdx", ".qcow2", ".vbox", ".vmx", ".pvm", ".hdd")
_reg(Categoria.REDES, "Packet-Tracer", ".pkt", ".pka")
_reg(Categoria.REDES, "GNS3", ".gns3", ".gns3project", ".gns3a")
_reg(Categoria.REDES, "Otros", ".vpc", ".unl", ".pcap", ".pcapng", ".cap", ".plb")
_reg(Categoria.JUEGOS, "Switch", ".nsp", ".nsz", ".xci", ".xcz")
_reg(Categoria.JUEGOS, "ROMs", ".nes", ".sfc", ".smc", ".gba", ".gb", ".gbc", ".n64", ".z64", ".v64", ".gcm", ".wbfs", ".cso", ".pbp", ".cia")
_reg(Categoria.JUEGOS, "Guardados", ".sav", ".srm", ".state")
_reg(Categoria.SUBTITULOS, SUBTIPO_DESCONOCIDO, ".srt", ".sub", ".idx", ".ass", ".ssa", ".vtt", ".sbv", ".smi")
_reg(Categoria.FUENTES, SUBTIPO_DESCONOCIDO, ".ttf", ".otf", ".woff", ".woff2", ".eot", ".fon", ".ttc")
_reg(Categoria.ATAJOS, SUBTIPO_DESCONOCIDO, ".lnk", ".url", ".desktop", ".webloc", ".website")
_reg(Categoria.TORRENTS, SUBTIPO_DESCONOCIDO, ".torrent", ".magnet", ".nzb")


def clasificar_archivo(ruta: Path) -> tuple[Categoria, str] | None:
    """Clasifica un archivo que no es audiovisual.

    Args:
        ruta: Ruta del archivo. Solo se mira su extension.

    Returns:
        `(categoria, subtipo)`, o `None` si la extension no esta en la tabla.
        `None` significa "sin clasificar", no "no organizable": quien llama
        decide si lo manda a `Otros-Archivos`.

    Examples:
        >>> clasificar_archivo(Path('informe.pdf'))
        (<Categoria.DOCUMENTOS: 'Documentos'>, 'PDF')
        >>> clasificar_archivo(Path('respaldo.SQL'))
        (<Categoria.DATOS: 'Bases-de-datos'>, 'Scripts-SQL')
        >>> clasificar_archivo(Path('practica.pkt'))
        (<Categoria.REDES: 'Redes'>, 'Packet-Tracer')
        >>> clasificar_archivo(Path('cosa.qwerty')) is None
        True
    """
    return _TABLA.get(ruta.suffix.casefold())


def extensiones_registradas() -> frozenset[str]:
    """Todas las extensiones que la taxonomia reconoce.

    Sirve para verificar que no se solape con la tabla audiovisual.
    """
    return frozenset(_TABLA)
