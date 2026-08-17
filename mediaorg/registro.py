"""Logging y barra de progreso sin dependencias externas."""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from .rutas_seguras import ruta_del_sistema

__all__ = ["configurar_logging", "Progreso", "formatear_bytes", "formatear_duracion"]

_FORMATO = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"
_FECHA = "%Y-%m-%d %H:%M:%S"


def configurar_logging(nivel: int, archivo: Path | None = None) -> logging.Logger:
    """Instala handlers de consola y (opcionalmente) de archivo.

    Args:
        nivel: Nivel del handler de consola.
        archivo: Ruta del log en disco. Siempre se escribe en DEBUG.

    Returns:
        El logger raiz del paquete.
    """
    raiz = logging.getLogger("mediaorg")
    raiz.setLevel(logging.DEBUG)
    for viejo in list(raiz.handlers):
        raiz.removeHandler(viejo)
        viejo.close()

    consola = logging.StreamHandler(stream=sys.stderr)
    consola.setLevel(nivel)
    consola.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    raiz.addHandler(consola)

    if archivo is not None:
        try:
            # `ruta_del_sistema` tambien aqui: es la unica escritura de la
            # corrida que no pasaba por el prefijo de ruta larga y reintroducia
            # el limite de 260 caracteres de Windows.
            os.makedirs(ruta_del_sistema(archivo.parent), exist_ok=True)
            disco = logging.FileHandler(ruta_del_sistema(archivo), encoding="utf-8")
            disco.setLevel(logging.DEBUG)
            disco.setFormatter(logging.Formatter(_FORMATO, datefmt=_FECHA))
            raiz.addHandler(disco)
        except OSError as exc:
            raiz.warning("No se pudo abrir el log %s: %s", archivo, exc)

    raiz.propagate = False
    return raiz


def formatear_bytes(cantidad: float) -> str:
    """`1536` -> `1.5 KB`."""
    unidades = ("B", "KB", "MB", "GB", "TB", "PB")
    valor = float(cantidad)
    for unidad in unidades:
        if abs(valor) < 1024.0 or unidad == unidades[-1]:
            return f"{valor:.1f} {unidad}" if unidad != "B" else f"{valor:.0f} B"
        valor /= 1024.0
    return f"{valor:.1f} PB"  # pragma: no cover - inalcanzable


def formatear_duracion(segundos: float) -> str:
    """`3725` -> `1h 02m 05s`."""
    segundos = max(0, int(segundos))
    horas, resto = divmod(segundos, 3600)
    minutos, segs = divmod(resto, 60)
    if horas:
        return f"{horas}h {minutos:02d}m {segs:02d}s"
    if minutos:
        return f"{minutos}m {segs:02d}s"
    return f"{segs}s"


class Progreso:
    """Indicador de progreso de una linea, escrito a stderr.

    Se desactiva solo cuando stderr no es una terminal (pipe, archivo,
    ejecucion no interactiva) para no ensuciar la salida capturada.
    """

    __slots__ = ("_etiqueta", "_total", "_hechos", "_inicio", "_ultimo", "_activo", "_intervalo")

    def __init__(self, etiqueta: str, total: int, *, intervalo: float = 0.25) -> None:
        self._etiqueta = etiqueta
        self._total = max(0, total)
        self._hechos = 0
        self._inicio = time.monotonic()
        self._ultimo = 0.0
        self._intervalo = intervalo
        self._activo = bool(getattr(sys.stderr, "isatty", lambda: False)())

    def avanzar(self, cantidad: int = 1) -> None:
        """Suma unidades procesadas y repinta si toca."""
        self._hechos += cantidad
        if not self._activo:
            return
        ahora = time.monotonic()
        if ahora - self._ultimo < self._intervalo and self._hechos < self._total:
            return
        self._ultimo = ahora
        self._pintar()

    def _pintar(self) -> None:
        transcurrido = max(1e-6, time.monotonic() - self._inicio)
        ritmo = self._hechos / transcurrido
        if self._total:
            pct = 100.0 * self._hechos / self._total
            faltan = (self._total - self._hechos) / ritmo if ritmo > 0 else 0.0
            texto = (
                f"  {self._etiqueta}: {self._hechos}/{self._total} ({pct:5.1f}%) "
                f"{ritmo:7.1f}/s  falta {formatear_duracion(faltan)}"
            )
        else:
            texto = f"  {self._etiqueta}: {self._hechos} ({ritmo:7.1f}/s)"
        sys.stderr.write(f"\r{texto[:118]:<118}")
        sys.stderr.flush()

    def cerrar(self) -> None:
        """Deja el cursor en una linea limpia."""
        if self._activo:
            self._pintar()
            sys.stderr.write("\n")
            sys.stderr.flush()
