"""Aplicacion del plan sobre el sistema de archivos, y su reversion.

Garantias:

* **Nada se borra.** Los duplicados se mueven a `BORRAR`; la decision de
  eliminarlos queda en manos de la persona.
* **Nada se sobreescribe.** Antes de cada movimiento se verifica que el
  destino no exista. En Windows `os.rename` falla si el destino existe, lo que
  da la garantia a nivel de sistema operativo.
* **Todo queda registrado.** Cada operacion se anota en un manifiesto JSONL
  con vaciado inmediato, de modo que una interrupcion abrupta deja igualmente
  un registro completo de lo ya hecho y `--deshacer` puede revertirlo.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final, TextIO

from .config import Configuracion, ModoOperacion
from .errores import ErrorDeEjecucion, ErrorDeManifiesto, RutaInsegura
from .planificador import Movimiento, Plan
from .registro import Progreso
from .rutas_seguras import (
    asegurar_directorio,
    es_subruta,
    mismo_volumen,
    normalizar,
    ruta_del_sistema,
)

__all__ = ["ResumenEjecucion", "ejecutar_plan", "deshacer", "VERSION_MANIFIESTO"]

_log = logging.getLogger(__name__)

#: Version del formato del manifiesto. `deshacer` rechaza versiones futuras.
VERSION_MANIFIESTO: Final = 1

_TIPO_ENCABEZADO: Final = "encabezado"
_TIPO_OPERACION: Final = "operacion"


@dataclass(slots=True)
class ResumenEjecucion:
    """Contadores del resultado real de la ejecucion."""

    aplicados: int = 0
    omitidos: int = 0
    fallidos: int = 0
    bytes_movidos: int = 0
    carpetas_creadas: int = 0
    errores: list[str] = field(default_factory=list)

    def registrar_error(self, mensaje: str, limite: int = 200) -> None:
        """Acumula el error acotando la lista para no crecer sin control."""
        self.fallidos += 1
        if len(self.errores) < limite:
            self.errores.append(mensaje)


def ejecutar_plan(plan: Plan, cfg: Configuracion, manifiesto: Path | None = None) -> ResumenEjecucion:
    """Materializa los movimientos del plan.

    Args:
        plan: Plan calculado por el planificador.
        cfg: Configuracion de la corrida.
        manifiesto: Ruta del JSONL de reversion. `None` para no registrar.

    Returns:
        Un `ResumenEjecucion` con lo efectivamente aplicado.

    Notes:
        En simulacion (`cfg.simulacion`) no se toca el disco: se devuelve el
        conteo de lo que se habria hecho.
    """
    resumen = ResumenEjecucion()
    if not plan.movimientos:
        return resumen

    if cfg.simulacion:
        resumen.omitidos = len(plan.movimientos)
        return resumen

    creadas: set[Path] = set()
    barra = Progreso("aplicando  ", len(plan.movimientos))
    diario: TextIO | None = None
    try:
        if manifiesto is not None:
            diario = _abrir_manifiesto(manifiesto, cfg)
        for movimiento in plan.movimientos:
            _aplicar_uno(movimiento, cfg, resumen, creadas, diario)
            barra.avanzar()
    finally:
        barra.cerrar()
        if diario is not None:
            diario.close()

    resumen.carpetas_creadas = len(creadas)
    return resumen


def _aplicar_uno(
    movimiento: Movimiento,
    cfg: Configuracion,
    resumen: ResumenEjecucion,
    creadas: set[Path],
    diario: TextIO | None,
) -> None:
    """Ejecuta un movimiento aislando cualquier fallo a ese archivo."""
    try:
        carpeta = movimiento.destino.parent
        if carpeta not in creadas:
            asegurar_directorio(carpeta)
            creadas.add(carpeta)
        if cfg.operacion is ModoOperacion.MOVER:
            _mover(movimiento.origen, movimiento.destino)
        else:
            _copiar(movimiento.origen, movimiento.destino)
    except (ErrorDeEjecucion, RutaInsegura, OSError) as exc:
        # `RutaInsegura` incluida a proposito: no hereda de OSError, y sin ella
        # un solo fallo al crear una carpeta abortaba el resto de la corrida.
        mensaje = f"{movimiento.origen} -> {movimiento.destino}: {exc}"
        resumen.registrar_error(mensaje)
        _log.warning("Fallo al %s %s", cfg.operacion.value, mensaje)
        return

    resumen.aplicados += 1
    resumen.bytes_movidos += movimiento.tamano
    if diario is not None:
        _anotar(diario, movimiento, cfg.operacion)


def _mover(origen: Path, destino: Path) -> None:
    """Mueve un archivo sin sobreescribir jamas el destino."""
    origen_os = ruta_del_sistema(origen)
    destino_os = ruta_del_sistema(destino)
    if os.path.lexists(destino_os):
        raise ErrorDeEjecucion("el destino ya existe")
    if mismo_volumen(origen, destino):
        # `os.rename` en Windows falla si el destino existe: es la operacion
        # segura. En el mismo volumen es un cambio de entrada de directorio,
        # instantaneo sea el archivo de 2 KB o de 2 GB.
        os.rename(origen_os, destino_os)
        return
    # Entre volumenes `shutil.move` es copiar y borrar, asi que puede dejar un
    # destino a medias si el disco se llena o el USB se desconecta.
    with _limpiar_si_falla(destino_os):
        shutil.move(origen_os, destino_os)


def _copiar(origen: Path, destino: Path) -> None:
    """Copia preservando fechas y verificando el tamano resultante."""
    origen_os = ruta_del_sistema(origen)
    destino_os = ruta_del_sistema(destino)
    if os.path.lexists(destino_os):
        raise ErrorDeEjecucion("el destino ya existe")
    with _limpiar_si_falla(destino_os):
        shutil.copy2(origen_os, destino_os)
        if os.stat(origen_os).st_size != os.stat(destino_os).st_size:
            raise ErrorDeEjecucion("la copia quedo con tamano distinto al original")


@contextmanager
def _limpiar_si_falla(destino_os: str) -> Iterator[None]:
    """Borra el destino a medio escribir si la operacion falla.

    Se sabe que el destino no existia antes (se comprobo justo arriba), asi que
    eliminarlo no puede destruir nada previo. Sin esto, un archivo truncado
    quedaria dentro del arbol organizado, sin registro en el manifiesto y por
    tanto invisible para `--deshacer`.
    """
    try:
        yield
    except BaseException:
        try:
            if os.path.lexists(destino_os):
                os.remove(destino_os)
        except OSError as limpieza:  # pragma: no cover - defensivo
            _log.warning("No se pudo limpiar el destino parcial %s: %s", destino_os, limpieza)
        raise


# --------------------------------------------------------------------------- #
# Manifiesto
# --------------------------------------------------------------------------- #

def _abrir_manifiesto(ruta: Path, cfg: Configuracion) -> TextIO:
    """Crea el manifiesto y escribe su encabezado."""
    asegurar_directorio(ruta.parent)
    manejador = open(ruta_del_sistema(ruta), "w", encoding="utf-8", newline="\n")
    encabezado = {
        "tipo": _TIPO_ENCABEZADO,
        "version": VERSION_MANIFIESTO,
        "creado": datetime.now().isoformat(timespec="seconds"),
        "raiz": str(cfg.raiz),
        "destino": str(cfg.destino_efectivo),
        "operacion": cfg.operacion.value,
        "jerarquia": cfg.jerarquia.value,
    }
    manejador.write(json.dumps(encabezado, ensure_ascii=False) + "\n")
    manejador.flush()
    return manejador


def _anotar(diario: TextIO, movimiento: Movimiento, operacion: ModoOperacion) -> None:
    """Registra una operacion aplicada, con vaciado inmediato."""
    registro = {
        "tipo": _TIPO_OPERACION,
        "op": operacion.value,
        "origen": str(movimiento.origen),
        "destino": str(movimiento.destino),
        "motivo": movimiento.motivo,
        "tamano": movimiento.tamano,
    }
    diario.write(json.dumps(registro, ensure_ascii=False) + "\n")
    diario.flush()


def deshacer(manifiesto: Path, simulacion: bool = True) -> ResumenEjecucion:
    """Revierte una corrida a partir de su manifiesto.

    Procesa las operaciones en orden inverso y solo revierte lo que sigue
    consistente: el destino debe existir y el origen debe estar libre. Un
    archivo que la persona ya movio o renombro a mano se omite en lugar de
    provocar un fallo.

    Args:
        manifiesto: JSONL generado por una ejecucion previa.
        simulacion: Si es True, solo informa lo que haria. Es el valor por
            defecto, igual que en el resto del paquete: nada toca el disco sin
            que se pida explicitamente.

    Returns:
        Un `ResumenEjecucion` con lo revertido.

    Raises:
        ErrorDeManifiesto: Si el archivo no existe, no es legible o su version
            no es compatible.
    """
    encabezado, operaciones, rechazadas = _leer_manifiesto(manifiesto)
    resumen = ResumenEjecucion()
    for aviso in rechazadas:
        resumen.omitidos += 1
        resumen.errores.append(aviso)
        _log.warning("%s", aviso)

    barra = Progreso("revirtiendo", len(operaciones))
    try:
        for registro in reversed(operaciones):
            _revertir_uno(registro, simulacion, resumen)
            barra.avanzar()
    finally:
        barra.cerrar()
    if not simulacion:
        frontera = normalizar(Path(encabezado["destino"]))
        _podar_vacias({Path(r["destino"]).parent for r in operaciones}, frontera)
    return resumen


def _leer_manifiesto(manifiesto: Path) -> tuple[dict, list[dict], list[str]]:
    """Carga y valida el manifiesto.

    Returns:
        `(encabezado, operaciones, rechazadas)`. Las operaciones devueltas
        tienen rutas normalizadas y verificadas contra la raiz y el destino que
        declara el propio encabezado. `rechazadas` trae un mensaje por fila
        descartada.

    Raises:
        ErrorDeManifiesto: Si falta el archivo, esta corrupto, no trae
            encabezado, su version es incompatible o no queda ninguna
            operacion aplicable.

    Notes:
        El manifiesto vive dentro del arbol de medios, en un disco que puede
        circular entre maquinas, asi que se trata como entrada no confiable:
        sin la comprobacion de contencion, una fila alterada haria que
        `--deshacer` moviera cualquier archivo del sistema.
    """
    try:
        contenido = Path(ruta_del_sistema(manifiesto)).read_text(encoding="utf-8")
    except OSError as exc:
        raise ErrorDeManifiesto(f"No se pudo leer el manifiesto {manifiesto}: {exc}") from exc

    encabezado: dict | None = None
    crudas: list[tuple[int, dict]] = []
    for numero, linea in enumerate(contenido.splitlines(), start=1):
        linea = linea.strip()
        if not linea:
            continue
        try:
            registro = json.loads(linea)
        except json.JSONDecodeError as exc:
            raise ErrorDeManifiesto(f"Linea {numero} corrupta en el manifiesto: {exc}") from exc
        if not isinstance(registro, dict):
            raise ErrorDeManifiesto(f"Linea {numero} no es un objeto JSON")
        if registro.get("tipo") == _TIPO_ENCABEZADO:
            if encabezado is not None:
                raise ErrorDeManifiesto(f"Linea {numero}: hay mas de un encabezado")
            encabezado = _validar_encabezado(registro)
            continue
        if registro.get("tipo") != _TIPO_OPERACION:
            continue
        if not isinstance(registro.get("origen"), str) or not isinstance(
            registro.get("destino"), str
        ):
            raise ErrorDeManifiesto(f"Linea {numero} sin rutas validas")
        crudas.append((numero, registro))

    if encabezado is None:
        raise ErrorDeManifiesto(f"El manifiesto no tiene encabezado: {manifiesto}")

    raiz = normalizar(Path(encabezado["raiz"]))
    destino = normalizar(Path(encabezado["destino"]))
    operaciones: list[dict] = []
    rechazadas: list[str] = []
    for numero, registro in crudas:
        origen = normalizar(Path(registro["origen"]))
        salida = normalizar(Path(registro["destino"]))
        if not es_subruta(salida, destino) or not es_subruta(origen, raiz):
            rechazadas.append(
                f"Linea {numero} del manifiesto fuera del arbol declarado, se omite: "
                f"{origen} <- {salida}"
            )
            continue
        operaciones.append({**registro, "origen": str(origen), "destino": str(salida)})

    if not operaciones:
        raise ErrorDeManifiesto(f"El manifiesto no contiene operaciones aplicables: {manifiesto}")
    return encabezado, operaciones, rechazadas


def _validar_encabezado(registro: dict) -> dict:
    """Comprueba la version y la presencia de las rutas de referencia."""
    version = registro.get("version")
    if not isinstance(version, int) or version > VERSION_MANIFIESTO:
        raise ErrorDeManifiesto(f"Version de manifiesto no soportada: {version!r}")
    for campo in ("raiz", "destino"):
        valor = registro.get(campo)
        if not isinstance(valor, str) or not valor:
            raise ErrorDeManifiesto(f"El encabezado del manifiesto no declara `{campo}`")
    return registro


def _revertir_uno(registro: dict, simulacion: bool, resumen: ResumenEjecucion) -> None:
    """Devuelve un archivo a su ubicacion original si es seguro hacerlo."""
    origen = Path(registro["origen"])
    destino = Path(registro["destino"])
    origen_os = ruta_del_sistema(origen)
    destino_os = ruta_del_sistema(destino)

    if not os.path.lexists(destino_os):
        resumen.omitidos += 1
        _log.debug("Ya no existe, se omite: %s", destino)
        return
    if os.path.lexists(origen_os):
        resumen.omitidos += 1
        _log.debug("El origen esta ocupado, se omite: %s", origen)
        return
    if simulacion:
        resumen.aplicados += 1  # en simulacion cuenta como "se revertiria"
        return

    try:
        asegurar_directorio(origen.parent)
        if mismo_volumen(destino, origen):
            os.rename(destino_os, origen_os)
        else:
            shutil.move(destino_os, origen_os)
    except OSError as exc:
        resumen.registrar_error(f"{destino} -> {origen}: {exc}")
        return

    resumen.aplicados += 1
    tamano = registro.get("tamano")
    if isinstance(tamano, int):
        resumen.bytes_movidos += tamano


def _podar_vacias(carpetas: set[Path], frontera: Path) -> None:
    """Elimina carpetas que quedaron vacias tras la reversion.

    Solo borra directorios vacios y sube hasta encontrar uno con contenido.
    Nunca elimina archivos, y nunca sube por encima de `frontera`, que es el
    destino declarado en el manifiesto.
    """
    for carpeta in sorted(carpetas, key=lambda p: len(p.parts), reverse=True):
        actual = carpeta
        while actual != frontera and es_subruta(actual, frontera):
            try:
                with os.scandir(ruta_del_sistema(actual)) as entradas:
                    if any(True for _ in entradas):
                        break
                os.rmdir(ruta_del_sistema(actual))
            except OSError:
                break
            padre = actual.parent
            if padre == actual:
                break
            actual = padre
