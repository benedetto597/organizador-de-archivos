"""Configuracion inmutable del organizador.

Un unico objeto `Configuracion` viaja por todo el pipeline. Es congelado:
ningun modulo puede mutar el comportamiento global a mitad de la corrida.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .errores import ConfiguracionInvalida

__all__ = [
    "Jerarquia",
    "JerarquiaArchivos",
    "ModoContenido",
    "ModoDuplicados",
    "ModoOperacion",
    "Configuracion",
    "CARPETA_PROYECTOS",
    "NOMBRES_SISTEMA_PROHIBIDOS",
    "CARPETAS_NO_TRANSITABLES",
    "ARCHIVOS_BASURA_DEL_SISTEMA",
    "MARCADORES_DE_UNIDAD",
    "MARCADORES_DE_UNIDAD_FUERTES",
    "CARPETA_HERRAMIENTA",
    "CARPETA_BORRAR",
    "CARPETA_TRABAJO",
    "CARPETA_SIN_FECHA",
    "LIMITE_ANIO_MINIMO",
    "TOPE_PROCESOS",
]

#: Nombre de la carpeta que contiene esta herramienta. Se excluye del escaneo
#: para que el organizador nunca se mueva a si mismo.
CARPETA_HERRAMIENTA = "organizador"

#: Destino de los duplicados. Nunca se borra nada: solo se aisla aqui.
CARPETA_BORRAR = "BORRAR"

#: Carpeta de logs, reportes y manifiestos.
CARPETA_TRABAJO = "_ORGANIZADOR"

#: Segmento usado cuando no se pudo determinar una fecha confiable.
CARPETA_SIN_FECHA = "Sin-Fecha"

#: Fechas anteriores a este anio se consideran basura de metadatos.
LIMITE_ANIO_MINIMO = 1990

#: Carpeta donde se listan las subcarpetas que se dejaron sin tocar.
CARPETA_PROYECTOS = "Proyectos"

#: Nombres de carpeta que el programa se niega a organizar, en cualquier nivel
#: de la ruta. Reorganizar cualquiera de estas rompe el sistema operativo, las
#: aplicaciones instaladas o los datos de perfil de las aplicaciones.
NOMBRES_SISTEMA_PROHIBIDOS = frozenset(
    {
        "windows", "winnt", "program files", "program files (x86)", "programdata",
        "appdata", "application data", "$recycle.bin", "recycler",
        "system volume information", "recovery", "$windows.~bt", "$windows.~ws",
        "msocache", "perflogs", "config.msi", "boot", "efi", "system32",
        "windowsapps", "onedrivetemp", ".git", "node_modules", "site-packages",
    }
    | {f"found.{i:03d}" for i in range(10)}
)

#: Nombres de carpeta en los que el recorrido NO entra nunca, en ningun nivel.
#: `NOMBRES_SISTEMA_PROHIBIDOS` protege el punto de partida; esto protege cada
#: descenso. Sin este segundo control, organizar la carpeta de perfil movia
#: imagenes desde `AppData\\Roaming` y desarmaba `site-packages`, `node_modules`
#: y `__pycache__`, porque ninguna de esas carpetas tiene marcador propio.
#: Solo se listan nombres que son inequivocamente artefactos de herramientas o
#: del sistema: `build`, `dist` o `bin` quedan fuera a proposito, porque una
#: persona puede tener una carpeta suya con ese nombre llena de archivos reales.
#: Archivos que el sistema operativo genera y que no son del usuario. Moverlos
#: es contraproducente: `desktop.ini` guarda la apariencia de la carpeta que lo
#: contiene, y `Thumbs.db` se regenera solo.
ARCHIVOS_BASURA_DEL_SISTEMA = frozenset(
    {
        "thumbs.db", "ehthumbs.db", "ehthumbs_vista.db", "desktop.ini",
        ".ds_store", ".localized", "icon\r", "folder.jpg", "albumart.ini",
        "iconcache.db", "ntuser.dat", "ntuser.ini", ".directory",
    }
)

CARPETAS_NO_TRANSITABLES = NOMBRES_SISTEMA_PROHIBIDOS | frozenset(
    {
        ".svn", ".hg", ".venv", "venv", "__pycache__", ".tox", ".gradle",
        ".idea", ".vs", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        ".next", ".nuxt", "bower_components", ".terraform", ".npm", ".nuget",
        ".cache", ".conda", ".ipynb_checkpoints", ".dart_tool", "obj",
    }
)

#: Subconjunto de marcadores que por si solos prueban que la carpeta ES un
#: proyecto, no que este cerca de uno. Se usan para RECHAZAR la carpeta de
#: inicio, donde un falso positivo es caro: una carpeta de fotos con un
#: `.gitignore` suelto no es un proyecto, pero una con `package.json` si.
MARCADORES_DE_UNIDAD_FUERTES = frozenset(
    {
        ".git", ".svn", ".hg", "node_modules", ".venv",
        "package.json", "requirements.txt", "pyproject.toml", "setup.py",
        "cargo.toml", "go.mod", "pom.xml", "build.gradle", "composer.json",
        "gemfile", "__manifest__.py", "__openerp__.py", "pubspec.yaml",
        "mix.exs", "unins000.exe", "uninstall.exe", "app.asar",
    }
)

#: Marcadores que identifican la raiz de un proyecto o de una unidad que no se
#: debe desarmar. Presencia de cualquiera => la carpeta viaja entera o no se
#: toca, pero nunca se le sacan archivos por dentro.
MARCADORES_DE_UNIDAD = frozenset(
    {
        ".git", ".svn", ".hg", ".gitignore", "package.json", "requirements.txt",
        "pyproject.toml", "setup.py", "setup.cfg", "cargo.toml", "go.mod",
        "pom.xml", "build.gradle", "build.gradle.kts", "composer.json", "gemfile",
        "dockerfile", "docker-compose.yml", "makefile", "cmakelists.txt",
        "__manifest__.py", "__openerp__.py", "node_modules", ".venv",
        "pubspec.yaml", "mix.exs", "package-lock.json", "yarn.lock",
        "unins000.exe", "uninstall.exe", "uninstal.exe", "app.asar",
        "manifest.json", ".sln", ".csproj", "gradlew", "mvnw",
        # Los gestores de paquetes dejan estos con punto delante dentro de
        # `node_modules`, asi que la raiz se reconoce sola.
        ".package-lock.json", ".yarn-integrity", "pipfile", "poetry.lock",
        "pnpm-lock.yaml", "bun.lockb", "gemfile.lock", "composer.lock",
    }
)


class Jerarquia(str, Enum):
    """Forma del arbol de carpetas que se construye."""

    TIPO_FECHA_FUENTE = "tipo-fecha-fuente"
    TIPO_FECHA = "tipo-fecha"
    TIPO_FUENTE = "tipo-fuente"
    FECHA = "fecha"
    TIPO = "tipo"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class JerarquiaArchivos(str, Enum):
    """Forma del arbol para lo que no es foto ni video.

    No se reutiliza `Jerarquia` porque los ejes son distintos: un documento no
    tiene "fuente" ni mes de captura, pero si tiene categoria y subtipo.
    """

    CATEGORIA_SUBTIPO_ANIO = "categoria-subtipo-anio"
    CATEGORIA_ANIO = "categoria-anio"
    CATEGORIA = "categoria"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class ModoDuplicados(str, Enum):
    """Profundidad de la deteccion de copias."""

    NINGUNO = "ninguno"
    BYTE = "byte"
    PIXEL = "pixel"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class ModoOperacion(str, Enum):
    """Que hacer con el archivo original."""

    MOVER = "mover"
    COPIAR = "copiar"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class ModoContenido(str, Enum):
    """Que clase de archivos entran al plan."""

    #: Solo fotos, videos y animaciones. Recorre todo el arbol.
    MEDIA = "media"
    #: Solo lo que no es audiovisual: documentos, comprimidos, instaladores...
    #: Por defecto se limita al primer nivel, que es lo seguro en un disco con
    #: proyectos y programas instalados.
    ARCHIVOS = "archivos"
    #: Ambos, con el limite de profundidad conservador de `archivos`.
    TODO = "todo"

    @property
    def incluye_media(self) -> bool:
        """True si el modo procesa fotos y videos."""
        return self in (ModoContenido.MEDIA, ModoContenido.TODO)

    @property
    def incluye_archivos(self) -> bool:
        """True si el modo procesa lo que no es audiovisual."""
        return self in (ModoContenido.ARCHIVOS, ModoContenido.TODO)

    @property
    def profundidad_por_defecto(self) -> int:
        """Niveles a recorrer si no se indica `--profundidad`.

        `media` recorre sin limite porque una foto anidada sigue siendo una
        foto y moverla no rompe nada. Los otros modos se quedan en el primer
        nivel: en un disco real las subcarpetas suelen ser proyectos, juegos o
        programas, y sacarles archivos por dentro los deja inservibles.
        """
        return 0 if self is ModoContenido.MEDIA else 1

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


#: Tope de procesos por defecto. Acota la memoria: cada trabajador puede tener
#: una imagen decodificada en RAM al mismo tiempo.
TOPE_PROCESOS = 16


def _rechazar_ruta_de_sistema(raiz: Path) -> None:
    """Impide operar sobre carpetas del sistema o de aplicaciones instaladas.

    Se comprueba cada componente de la ruta, no solo el ultimo: organizar
    `C:\\Windows\\System32` y organizar `D:\\copia\\Windows\\System32` son
    igual de destructivos.

    Raises:
        ConfiguracionInvalida: Si la ruta cae en una zona prohibida.
    """
    from .rutas_seguras import es_subruta  # import local: evita un ciclo

    partes = {parte.casefold() for parte in raiz.parts}
    prohibidas = partes & NOMBRES_SISTEMA_PROHIBIDOS
    # La carpeta temporal del sistema vive dentro de `AppData\Local\Temp`, y
    # organizar ahi es inofensivo. Solo se perdona ese disparador concreto: si
    # la ruta toca ademas `windows`, `program files` u otro, sigue bloqueada.
    if prohibidas == {"appdata"}:
        try:
            temporal = Path(tempfile.gettempdir()).resolve()
        except OSError:  # pragma: no cover - defensivo
            temporal = None
        if temporal is not None and es_subruta(raiz, temporal):
            prohibidas = set()
    if prohibidas:
        raise ConfiguracionInvalida(
            f"Ruta protegida, el organizador no opera ahi: {raiz} "
            f"(contiene {sorted(prohibidas)[0]!r}). "
            "Reorganizar carpetas de sistema o de programas instalados los rompe."
        )
    sistema = os.environ.get("SystemDrive", "C:")
    if raiz.parent == raiz and raiz.drive.casefold() == sistema.casefold():
        raise ConfiguracionInvalida(
            f"No se organiza la raiz del disco del sistema ({raiz}). "
            "Indica una carpeta concreta, por ejemplo la de Descargas."
        )


def _rechazar_raiz_que_es_unidad(raiz: Path) -> None:
    """Impide organizar una carpeta que es la raiz de un proyecto.

    El recorrido protege las subcarpetas que son unidades, pero la carpeta de
    inicio quedaba exenta: apuntar la herramienta a un repositorio movia su
    `README.md` a `Documentos\\Texto\\`, sus `.py` a `Codigo\\Python\\` y hasta el
    propio `requirements.txt` que lo identificaba como proyecto.

    Raises:
        ConfiguracionInvalida: Si la carpeta trae un marcador de unidad.
    """
    from .escaneo import marcador_de_unidad  # import local: evita un ciclo

    marcador = marcador_de_unidad(raiz, solo_fuertes=True)
    if marcador is None:
        return
    raise ConfiguracionInvalida(
        f"La carpeta indicada es la raiz de un proyecto ({marcador!r} esta dentro): "
        f"{raiz}\nOrganizarla lo desarmaria. Indica una carpeta contenedora, o pasa "
        "--forzar-proyecto si de verdad es lo que quieres."
    )


def _workers_por_defecto() -> int:
    """Elige el paralelismo por defecto para una carga limitada por disco.

    Medido sobre el caso real (57.701 archivos en un HDD por USB): el costo
    dominante es la latencia del primer acceso a cada archivo, no la CPU. Con
    8 nucleos, subir de 7 a 16 procesos bajo el costo por archivo de 62,8 ms a
    31,2 ms, porque el disco atiende varias peticiones encoladas a la vez.

    Por eso se sobresuscriben los nucleos, con un tope que acota la memoria.
    """
    nucleos = os.cpu_count() or 2
    return max(2, min(TOPE_PROCESOS, nucleos * 2))


@dataclass(frozen=True, slots=True)
class Configuracion:
    """Parametros efectivos de una corrida.

    Args:
        raiz: Carpeta a organizar. Debe existir y ser un directorio.
        destino: Raiz del arbol de salida. Por defecto la misma `raiz`
            (reorganizacion in-place, sin costo de espacio).
        operacion: Mover (instantaneo en el mismo volumen) o copiar.
        simulacion: Si es True no se toca el disco; solo se calcula el plan.
        jerarquia: Forma del arbol de carpetas.
        duplicados: Profundidad de la deteccion de copias.
        procesos: Trabajadores para las fases con uso intensivo de CPU/IO.
        mes_con_nombre: `2024-05-Mayo` en lugar de `2024-05`.
        prefiltro_rapido: Prefiltro DCT para JPEG en la fase de pixeles.
            Acelera mucho a cambio de un riesgo teorico de falso negativo.
        reprocesar: Incluir en el escaneo carpetas ya organizadas.
        escribir_manifiesto: Registrar cada movimiento para poder revertir.
        extensiones_extra: Extensiones adicionales a considerar media.
        excluir: Nombres de carpeta a ignorar (ademas de las internas).
        limite: Tope de archivos a procesar (0 = sin tope). Util para pruebas.
        tamano_minimo: Archivos mas pequenos que esto se ignoran.
        seguir_enlaces: Recorrer symlinks/junctions (desactivado por seguridad).
        incluir_ocultos: Procesar archivos ocultos, de sistema y los que
            empiezan por punto. Desactivado: mover `desktop.ini` cambia el
            aspecto de una carpeta y `Thumbs.db` es basura regenerable.
        forzar_proyecto: Permitir que la carpeta de inicio sea la raiz de un
            proyecto. Desactivado, porque organizarla lo desarma.
    """

    raiz: Path
    destino: Path | None = None
    operacion: ModoOperacion = ModoOperacion.MOVER
    simulacion: bool = True
    modo: ModoContenido = ModoContenido.MEDIA
    profundidad: int | None = None
    jerarquia: Jerarquia = Jerarquia.TIPO_FECHA_FUENTE
    jerarquia_archivos: JerarquiaArchivos = JerarquiaArchivos.CATEGORIA_SUBTIPO_ANIO
    duplicados: ModoDuplicados = ModoDuplicados.PIXEL
    procesos: int = field(default_factory=_workers_por_defecto)
    mes_con_nombre: bool = False
    prefiltro_rapido: bool = False
    reprocesar: bool = False
    escribir_manifiesto: bool = True
    extensiones_extra: frozenset[str] = frozenset()
    excluir: frozenset[str] = frozenset()
    limite: int = 0
    tamano_minimo: int = 1
    seguir_enlaces: bool = False
    incluir_ocultos: bool = False
    forzar_proyecto: bool = False

    def __post_init__(self) -> None:
        raiz = Path(self.raiz).expanduser()
        try:
            raiz = raiz.resolve(strict=True)
        except OSError as exc:
            raise ConfiguracionInvalida(f"No se puede acceder a la ruta: {self.raiz} ({exc})") from exc
        if not raiz.is_dir():
            raise ConfiguracionInvalida(f"La ruta no es una carpeta: {raiz}")
        _rechazar_ruta_de_sistema(raiz)
        if not self.forzar_proyecto:
            _rechazar_raiz_que_es_unidad(raiz)
        object.__setattr__(self, "raiz", raiz)

        if self.destino is None:
            object.__setattr__(self, "destino", raiz)
        else:
            destino = Path(self.destino).expanduser()
            try:
                destino = destino.resolve()
            except OSError as exc:
                raise ConfiguracionInvalida(f"Destino invalido: {self.destino} ({exc})") from exc
            if destino.exists() and not destino.is_dir():
                raise ConfiguracionInvalida(f"El destino existe y no es carpeta: {destino}")
            # El destino tambien se valida: volcar un acervo dentro de
            # `C:\Windows\System32` es tan destructivo como organizar esa
            # carpeta, y hasta ahora solo se comprobaba el origen.
            _rechazar_ruta_de_sistema(destino)
            if destino.is_dir() and not self.forzar_proyecto:
                _rechazar_raiz_que_es_unidad(destino)
            object.__setattr__(self, "destino", destino)

        if self.profundidad is None:
            object.__setattr__(self, "profundidad", self.modo.profundidad_por_defecto)
        elif self.profundidad < 0:
            raise ConfiguracionInvalida("La profundidad no puede ser negativa")

        if self.procesos < 1:
            raise ConfiguracionInvalida("El numero de procesos debe ser >= 1")
        if self.limite < 0:
            raise ConfiguracionInvalida("El limite no puede ser negativo")
        if self.tamano_minimo < 0:
            raise ConfiguracionInvalida("El tamano minimo no puede ser negativo")

        object.__setattr__(
            self,
            "extensiones_extra",
            frozenset(self._normalizar_extension(e) for e in self.extensiones_extra),
        )
        object.__setattr__(self, "excluir", frozenset(n.casefold() for n in self.excluir if n))

    @staticmethod
    def _normalizar_extension(extension: str) -> str:
        limpia = extension.strip().casefold()
        if not limpia:
            raise ConfiguracionInvalida("Extension vacia en --incluir-ext")
        return limpia if limpia.startswith(".") else f".{limpia}"

    @property
    def carpeta_trabajo(self) -> Path:
        """Donde viven logs, reportes y manifiestos de esta corrida."""
        return self.destino_efectivo / CARPETA_TRABAJO

    @property
    def carpeta_borrar(self) -> Path:
        """Cuarentena de duplicados."""
        return self.destino_efectivo / CARPETA_BORRAR

    @property
    def destino_efectivo(self) -> Path:
        """Destino garantizado no nulo (post-validacion siempre lo esta)."""
        assert self.destino is not None  # garantizado por __post_init__
        return self.destino

    def nombres_excluidos(self) -> frozenset[str]:
        """Nombres de carpeta a ignorar a cualquier profundidad.

        Son solo los que la persona pidio con `--excluir`. Las carpetas propias
        del organizador se excluyen por ruta exacta, no por nombre: excluirlas
        por nombre haria que una subcarpeta preexistente llamada `Fotos` se
        saltara del escaneo.
        """
        return self.excluir

    def rutas_excluidas(self) -> frozenset[Path]:
        """Carpetas concretas que el escaneo nunca debe recorrer.

        Son las salidas del propio organizador y la carpeta donde vive esta
        herramienta. Excluirlas es lo que hace la corrida idempotente: correrla
        dos veces no reorganiza lo ya organizado, salvo que se pida
        `reprocesar`, que solo reincorpora las carpetas por tipo.
        """
        # Imports locales: evitan un ciclo de importacion con estos modulos.
        from .categorias import Categoria
        from .tipos import TipoMedia

        destino = self.destino_efectivo
        rutas = {
            destino / CARPETA_TRABAJO,
            destino / CARPETA_BORRAR,
            destino / CARPETA_HERRAMIENTA,
            self.raiz / CARPETA_HERRAMIENTA,
            self.raiz / CARPETA_TRABAJO,
            # La carpeta real donde vive el paquete, por si se ejecuta desde
            # dentro del arbol que se esta organizando con otro nombre.
            Path(__file__).resolve().parents[1],
        }
        if not self.reprocesar:
            # Solo se excluyen las carpetas que ESTE modo produce. Excluirlas
            # todas siempre hacia que `--modo media` se saltara una carpeta
            # preexistente llamada `Documentos` o `Juegos`, dejando sin
            # organizar las fotos que hubiera dentro y sin decirlo.
            if self.modo.incluye_media:
                rutas.update(destino / tipo.carpeta for tipo in TipoMedia)
            if self.modo.incluye_archivos:
                rutas.update(destino / categoria.carpeta for categoria in Categoria)
        return frozenset(rutas)
