"""Recorrido del arbol de origen.

Usa `os.scandir`, que en Windows obtiene tamano y fecha de modificacion del
mismo registro del directorio: para 57.000 archivos eso ahorra decenas de
miles de llamadas `stat` adicionales. El escaneo no abre archivos.

Reglas de seguridad del recorrido:

* **Profundidad acotada.** En modo `archivos` el recorrido se queda en el
  primer nivel. En un disco real las subcarpetas son proyectos, juegos o
  programas instalados, y sacarles archivos por dentro los deja inservibles.
* **Unidades indivisibles.** Una carpeta con `.git`, `package.json`,
  `requirements.txt`, `__manifest__.py`, `unins000.exe` y companiaes una unidad:
  se registra y se salta completa, nunca se le extraen archivos.
* No se siguen enlaces simbolicos ni junctions salvo peticion explicita.
* Se salta la carpeta de la propia herramienta y las carpetas de salida.
* Los errores de permisos se registran y no abortan el recorrido.
"""

from __future__ import annotations

import logging
import os
import stat as _stat
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .config import CARPETAS_NO_TRANSITABLES, MARCADORES_DE_UNIDAD, Configuracion
from .rutas_seguras import ruta_del_sistema
from .tipos import TipoMedia, tipo_por_extension

__all__ = ["ArchivoEncontrado", "UnidadDetectada", "ResumenEscaneo", "escanear"]

_log = logging.getLogger(__name__)

#: Atributo de Windows que marca symlinks, junctions y puntos de montaje. El
#: modulo `stat` solo lo define en Windows, de ahi el valor de respaldo.
_REPARSE_POINT = getattr(_stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass(frozen=True, slots=True)
class ArchivoEncontrado:
    """Un archivo candidato con los datos baratos ya recolectados."""

    ruta: Path
    tamano: int
    mtime: float
    tipo: TipoMedia
    #: `st_birthtime` cuando el sistema lo expone. Se recoge aqui porque la
    #: entrada de directorio ya lo trae y evita un `stat` extra por archivo.
    nacimiento: float | None = None

    @property
    def nombre(self) -> str:
        """Nombre del archivo con extension."""
        return self.ruta.name

    @property
    def es_media(self) -> bool:
        """True si la extension lo declara audiovisual."""
        return self.tipo is not TipoMedia.DESCONOCIDO


@dataclass(frozen=True, slots=True)
class UnidadDetectada:
    """Carpeta que se dejo intacta porque es una unidad, no un contenedor."""

    ruta: Path
    marcador: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.ruta}  [{self.marcador}]"


@dataclass(slots=True)
class ResumenEscaneo:
    """Contadores de lo que ocurrio durante el recorrido."""

    archivos: int = 0
    aceptados: int = 0
    aceptados_media: int = 0
    aceptados_archivos: int = 0
    ignorados_extension: int = 0
    ignorados_tamano: int = 0
    enlaces_omitidos: int = 0
    carpetas: int = 0
    errores: int = 0
    bytes_aceptados: int = 0
    #: Subcarpetas que no se recorrieron por el limite de profundidad.
    carpetas_sin_recorrer: int = 0
    #: Carpetas de sistema o de herramientas en las que no se entro nunca.
    carpetas_de_sistema_omitidas: int = 0
    unidades: list[UnidadDetectada] = field(default_factory=list)


def _es_enlace(entrada: os.DirEntry[str]) -> bool:
    """Detecta symlinks y, en Windows, tambien junctions/reparse points."""
    try:
        if entrada.is_symlink():
            return True
    except OSError:  # pragma: no cover - defensivo
        return True
    if os.name != "nt":
        return False
    try:
        atributos = entrada.stat(follow_symlinks=False).st_file_attributes
    except (OSError, AttributeError):  # pragma: no cover - defensivo
        return False
    return bool(atributos & _REPARSE_POINT)


def _identidad(ruta: Path) -> tuple[int, int] | None:
    """Identidad del directorio en su volumen: `(st_dev, st_ino)`.

    Exige un `os.stat` real: en Windows los valores que trae `DirEntry.stat()`
    salen del cache de la entrada de directorio y llegan en cero, que no
    distingue una carpeta de otra.
    """
    try:
        info = os.stat(ruta_del_sistema(ruta))
    except OSError:
        return None
    return (info.st_dev, info.st_ino)


#: Tope de subcarpetas que se inspeccionan al buscar un marcador un nivel mas
#: abajo. Acota el costo en carpetas con cientos de hijos.
_MAX_HIJOS_INSPECCIONADOS = 20


def _inspeccionar(carpeta: Path) -> tuple[str | None, list[Path]]:
    """Lee un directorio una vez: devuelve su marcador y sus subcarpetas."""
    subcarpetas: list[Path] = []
    try:
        with os.scandir(ruta_del_sistema(carpeta)) as entradas:
            for entrada in entradas:
                nombre = entrada.name.casefold()
                if nombre in MARCADORES_DE_UNIDAD:
                    return entrada.name, subcarpetas
                # `.sln` y `.csproj` se reconocen por sufijo, no por nombre.
                if nombre.endswith((".sln", ".csproj", ".vcxproj", ".pyproj")):
                    return entrada.name, subcarpetas
                try:
                    if entrada.is_dir(follow_symlinks=False):
                        subcarpetas.append(carpeta / entrada.name)
                except OSError:  # pragma: no cover - defensivo
                    continue
    except OSError as exc:
        _log.debug("No se pudo inspeccionar %s: %s", carpeta, exc)
    return None, subcarpetas


def marcador_de_unidad(carpeta: Path, niveles_extra: int = 0) -> str | None:
    """Devuelve el marcador que hace indivisible a `carpeta`, o `None`.

    Args:
        carpeta: Carpeta a examinar.
        niveles_extra: Cuantos niveles adicionales mirar cuando la carpeta no
            trae marcador propio. Un modulo descargado y descomprimido suele
            quedar como `descarga/modulo_real/__manifest__.py`, asi que un
            nivel extra reconoce ese caso. Cuesta un `scandir` por subcarpeta,
            de modo que el recorrido recursivo lo deja en cero: ahi la unidad se
            detecta igual al bajar hasta su raiz.

    Returns:
        El nombre del marcador encontrado, prefijado por la subcarpeta si se
        hallo en un nivel inferior. `None` si la carpeta es un contenedor
        corriente.
    """
    marcador, subcarpetas = _inspeccionar(carpeta)
    if marcador is not None:
        return marcador
    if niveles_extra <= 0:
        return None
    for hijo in subcarpetas[:_MAX_HIJOS_INSPECCIONADOS]:
        interno = marcador_de_unidad(hijo, niveles_extra - 1)
        if interno is not None:
            return f"{hijo.name}/{interno}"
    return None


def escanear(
    cfg: Configuracion, resumen: ResumenEscaneo | None = None
) -> Iterator[ArchivoEncontrado]:
    """Recorre `cfg.raiz` y produce los archivos que el modo acepta.

    Args:
        cfg: Configuracion de la corrida.
        resumen: Contador opcional que se actualiza en sitio.

    Yields:
        Un `ArchivoEncontrado` por archivo aceptado.
    """
    contador = resumen if resumen is not None else ResumenEscaneo()
    nombres_excluidos = cfg.nombres_excluidos()
    rutas_excluidas = cfg.rutas_excluidas()
    profundidad = cfg.profundidad or 0
    pendientes: list[tuple[Path, int]] = [(cfg.raiz, 0)]
    entregados = 0
    # Solo con `--seguir-enlaces` puede haber ciclos, asi que el control de
    # visitados (un `stat` real por carpeta) se paga unicamente en ese caso.
    visitados: set[tuple[int, int]] | None = set() if cfg.seguir_enlaces else None

    while pendientes:
        carpeta, nivel = pendientes.pop()
        if visitados is not None:
            identidad = _identidad(carpeta)
            if identidad is not None:
                if identidad in visitados:
                    _log.debug("Carpeta ya visitada, posible ciclo de enlaces: %s", carpeta)
                    continue
                visitados.add(identidad)

        if nivel > 0:
            marcador = marcador_de_unidad(carpeta)
            if marcador is not None:
                contador.unidades.append(UnidadDetectada(carpeta, marcador))
                _log.debug("Unidad intacta (%s): %s", marcador, carpeta)
                continue

        contador.carpetas += 1
        try:
            with os.scandir(ruta_del_sistema(carpeta)) as entradas:
                for entrada in entradas:
                    if cfg.limite and entregados >= cfg.limite:
                        return
                    try:
                        es_dir = entrada.is_dir(follow_symlinks=cfg.seguir_enlaces)
                    except OSError as exc:
                        contador.errores += 1
                        _log.debug("No se pudo inspeccionar %s: %s", entrada.path, exc)
                        continue

                    if not cfg.seguir_enlaces and _es_enlace(entrada):
                        contador.enlaces_omitidos += 1
                        continue

                    # La ruta se compone desde el objeto padre y NO desde
                    # `entrada.path`: al abrir el directorio se le paso la ruta
                    # con prefijo `\\?\`, asi que `entrada.path` lo arrastra y
                    # contaminaria los destinos, los reportes y la comparacion
                    # de "ya esta en su lugar".
                    hijo = carpeta / entrada.name

                    if es_dir:
                        nombre_plano = entrada.name.casefold()
                        # Control por nombre en CADA descenso. La validacion de
                        # `Configuracion` protege solo el punto de partida: sin
                        # esto, organizar la carpeta de perfil sacaba imagenes
                        # de `AppData` y desarmaba `site-packages` y
                        # `node_modules`, que no tienen marcador propio.
                        if nombre_plano in CARPETAS_NO_TRANSITABLES:
                            contador.carpetas_de_sistema_omitidas += 1
                            _log.debug("Carpeta intocable, no se entra: %s", hijo)
                            continue
                        if nombre_plano in nombres_excluidos or hijo in rutas_excluidas:
                            _log.debug("Carpeta excluida: %s", hijo)
                            continue
                        if profundidad and (nivel + 1) >= profundidad:
                            contador.carpetas_sin_recorrer += 1
                            _registrar_unidad_superficial(hijo, contador)
                            continue
                        pendientes.append((hijo, nivel + 1))
                        continue

                    candidato = _evaluar(hijo, entrada, cfg, contador)
                    if candidato is not None:
                        entregados += 1
                        yield candidato
        except (PermissionError, FileNotFoundError, NotADirectoryError) as exc:
            contador.errores += 1
            _log.warning("Carpeta inaccesible %s: %s", carpeta, exc)
        except OSError as exc:
            contador.errores += 1
            _log.warning("Error recorriendo %s: %s", carpeta, exc)


def _registrar_unidad_superficial(carpeta: Path, contador: ResumenEscaneo) -> None:
    """Anota si una carpeta que no se recorrio es, ademas, una unidad.

    Aporta informacion al reporte sin cambiar el comportamiento: la carpeta se
    deja intacta de todas formas por el limite de profundidad. Como no se va a
    recorrer, aqui si vale la pena mirar un nivel mas abajo.
    """
    marcador = marcador_de_unidad(carpeta, niveles_extra=1)
    if marcador is not None:
        contador.unidades.append(UnidadDetectada(carpeta, marcador))


def _evaluar(
    ruta: Path,
    entrada: os.DirEntry[str],
    cfg: Configuracion,
    contador: ResumenEscaneo,
) -> ArchivoEncontrado | None:
    """Aplica los filtros de tamano, tipo y modo a un archivo concreto."""
    contador.archivos += 1
    try:
        info = entrada.stat(follow_symlinks=False)
    except OSError as exc:
        contador.errores += 1
        _log.debug("stat fallido en %s: %s", ruta, exc)
        return None

    if info.st_size < cfg.tamano_minimo:
        contador.ignorados_tamano += 1
        return None

    # Filtro por extension unicamente: abrir el archivo aqui costaria ~100 ms
    # por archivo en un disco USB, en serie. La firma binaria se verifica en la
    # fase de analisis, que corre en paralelo y puede refinar el tipo.
    tipo = tipo_por_extension(ruta, cfg.extensiones_extra)
    es_media = tipo is not TipoMedia.DESCONOCIDO
    extra = ruta.suffix.casefold() in cfg.extensiones_extra

    if es_media:
        if not cfg.modo.incluye_media:
            contador.ignorados_extension += 1
            return None
    else:
        if not (cfg.modo.incluye_archivos or extra):
            contador.ignorados_extension += 1
            return None

    nacimiento = getattr(info, "st_birthtime", None)
    contador.aceptados += 1
    if es_media:
        contador.aceptados_media += 1
    else:
        contador.aceptados_archivos += 1
    contador.bytes_aceptados += info.st_size
    return ArchivoEncontrado(
        ruta=ruta,
        tamano=info.st_size,
        mtime=info.st_mtime,
        tipo=tipo,
        nacimiento=float(nacimiento) if isinstance(nacimiento, (int, float)) else None,
    )
