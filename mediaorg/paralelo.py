"""Ejecucion paralela con degradacion elegante a modo serial.

Todas las fases costosas (leer metadatos, hashear bytes, decodificar pixeles)
son vergonzosamente paralelas. Este modulo centraliza el manejo del pool para
que ninguna fase tenga que repetir la logica de progreso, cancelacion y
recuperacion ante un pool roto.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Final, TypeVar

from .registro import Progreso

__all__ = ["mapear", "tamano_de_trozo", "procesos_efectivos", "TOPE_TRABAJADORES"]

_log = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

#: Con trozos muy pequenos el costo de IPC domina; con trozos muy grandes se
#: desbalancea la carga al final. 16-256 elementos funciona bien en la practica.
_TROZO_MINIMO = 16
_TROZO_MAXIMO = 256


def tamano_de_trozo(total: int, procesos: int) -> int:
    """Calcula un `chunksize` razonable para `Executor.map`."""
    if procesos <= 1 or total <= 0:
        return 1
    estimado = total // (procesos * 8) or 1
    return max(_TROZO_MINIMO, min(_TROZO_MAXIMO, estimado))


def mapear(
    funcion: Callable[[T], R],
    elementos: Sequence[T],
    *,
    procesos: int,
    etiqueta: str,
    mostrar_progreso: bool = True,
) -> Iterator[R]:
    """Aplica `funcion` a cada elemento, en paralelo cuando conviene.

    Args:
        funcion: Callable de nivel superior de modulo (requisito de `pickle`).
        elementos: Secuencia de entradas.
        procesos: Maximo de procesos. `1` fuerza modo serial.
        etiqueta: Texto de la barra de progreso.
        mostrar_progreso: Desactivar en pruebas para no ensuciar la salida.

    Yields:
        Los resultados en el mismo orden de entrada.

    Notes:
        Si el pool muere (memoria agotada, DLL de imagen que aborta el proceso)
        se registra el fallo y se reintenta el lote completo en serie, en lugar
        de perder la corrida entera.
    """
    total = len(elementos)
    if total == 0:
        return

    barra = Progreso(etiqueta, total) if mostrar_progreso else None
    # Se acota aqui, en el unico punto donde se crea el pool, para que tambien
    # quede protegido el uso como biblioteca y no solo el paso por el CLI.
    trabajadores = min(procesos, total, TOPE_TRABAJADORES) if procesos > 1 else 1

    try:
        if trabajadores <= 1:
            yield from _serial(funcion, elementos, barra)
        else:
            yield from _paralelo(funcion, elementos, trabajadores, barra)
    finally:
        if barra is not None:
            barra.cerrar()


#: Centinela para distinguir "el elemento fallo" de "el resultado es None".
_FALLO: Final = object()


def _aplicar_aislado(funcion: Callable[[T], R], elemento: T) -> R | object:
    """Ejecuta la funcion sin dejar que un elemento se lleve el lote entero."""
    try:
        return funcion(elemento)
    except Exception as exc:  # noqa: BLE001 - aislamiento deliberado por elemento
        _log.warning("Elemento omitido por error irrecuperable (%r): %r", elemento, exc)
        return _FALLO


def _serial(
    funcion: Callable[[T], R], elementos: Iterable[T], barra: Progreso | None
) -> Iterator[R]:
    """Camino sin procesos hijos."""
    for elemento in elementos:
        resultado = funcion(elemento)
        if barra is not None:
            barra.avanzar()
        yield resultado


def _paralelo(
    funcion: Callable[[T], R],
    elementos: Sequence[T],
    trabajadores: int,
    barra: Progreso | None,
) -> Iterator[R]:
    """Camino con `ProcessPoolExecutor` y respaldo serial."""
    trozo = tamano_de_trozo(len(elementos), trabajadores)
    procesados = 0
    try:
        with ProcessPoolExecutor(max_workers=trabajadores) as pool:
            for resultado in pool.map(funcion, elementos, chunksize=trozo):
                procesados += 1
                if barra is not None:
                    barra.avanzar()
                yield resultado
    except (BrokenProcessPool, OSError, MemoryError) as exc:
        # El trozo que estaba en vuelo contiene el elemento que mato al hijo,
        # y su posicion exacta dentro del trozo es desconocida. Reejecutarlo
        # aqui repetiria el aborto dentro del proceso padre, que es justo la
        # perdida total de la corrida que este respaldo debe evitar: por eso se
        # salta el trozo completo.
        reinicio = min(len(elementos), ((procesados // trozo) + 1) * trozo)
        omitidos = reinicio - procesados
        _log.warning(
            "Pool de procesos interrumpido tras %d/%d elementos (%s). Se omiten %d "
            "elementos del lote en vuelo y se continua en modo serial.",
            procesados,
            len(elementos),
            exc,
            omitidos,
        )
        if barra is not None and omitidos > 0:
            barra.avanzar(omitidos)
        for elemento in elementos[reinicio:]:
            resultado = _aplicar_aislado(funcion, elemento)
            if barra is not None:
                barra.avanzar()
            if resultado is not _FALLO:
                yield resultado


#: Windows no admite mas de 61 trabajadores en un `ProcessPoolExecutor`
#: (limite de `WaitForMultipleObjects`). Se deja margen bajo ese tope.
TOPE_TRABAJADORES = 60


def procesos_efectivos(solicitados: int) -> int:
    """Acota los procesos solicitados a un rango sensato.

    Se permite sobresuscribir los nucleos porque la carga esta limitada por la
    latencia del disco, no por la CPU, pero se pone un techo por dos razones:
    que un valor disparatado no agote la memoria con imagenes decodificadas, y
    que no se supere el maximo de trabajadores que admite Windows.
    """
    if solicitados < 1:
        return solicitados  # se deja pasar para que `Configuracion` lo rechace
    return min(solicitados, TOPE_TRABAJADORES)
