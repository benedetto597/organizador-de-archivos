"""Orquestacion del pipeline completo.

Fases, en orden:

1. **Escaneo**: recorre el arbol y filtra archivos audiovisuales.
2. **Analisis**: fecha, fuente y firma de imagen (paralelo, una apertura).
3. **Duplicados**: huella de bytes y de pixeles (paralelo, con prefiltros).
4. **Planificacion**: destino de cada archivo (puro, sin efectos).
5. **Reporte**: CSV del plan; se escribe siempre, tambien en simulacion.
6. **Ejecucion**: movimientos reales, con manifiesto de reversion.

Separar planificacion de ejecucion es lo que permite que la simulacion sea
fiel: no es una estimacion, es el mismo objeto `Plan` que luego se aplica.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from .config import Configuracion
from .duplicados import ResumenDuplicados, detectar_duplicados
from .ejecutor import ResumenEjecucion, ejecutar_plan
from .escaneo import ArchivoEncontrado, ResumenEscaneo, escanear
from .metadatos import Analisis, analizar
from .paralelo import mapear
from .planificador import Plan, construir_plan
from .registro import Progreso
from .reportes import Informe, escribir_csv, escribir_json, marca_tiempo

__all__ = ["organizar", "aplicar_plan_existente", "Autorizacion"]

_log = logging.getLogger(__name__)

#: Callback de autorizacion: recibe el plan y devuelve si se debe aplicar.
Autorizacion = Callable[[Plan], bool]


def organizar(cfg: Configuracion, autorizar: Autorizacion | None = None) -> Informe:
    """Ejecuta el pipeline completo y devuelve el informe de la corrida.

    Args:
        cfg: Configuracion de la corrida.
        autorizar: Callback opcional que recibe el plan ya calculado y decide
            si se materializa. Es el punto donde el CLI pide confirmacion:
            asi la pregunta se hace con el numero real de movimientos, no con
            una estimacion previa al analisis.
    """
    inicio = time.monotonic()
    sello = marca_tiempo()

    resumen_escaneo = ResumenEscaneo()
    items = _recolectar(cfg, resumen_escaneo)
    _log.info(
        "Escaneo: %s archivos audiovisuales de %s vistos en %s carpetas",
        f"{resumen_escaneo.aceptados:,}",
        f"{resumen_escaneo.archivos:,}",
        f"{resumen_escaneo.carpetas:,}",
    )
    if not items:
        # Se escribe el resumen igual: una corrida que no encontro nada tambien
        # es un resultado que conviene poder auditar despues.
        informe = _informe_vacio(cfg, resumen_escaneo, inicio)
        escribir_json(informe, cfg.carpeta_trabajo / f"resumen-{sello}.json")
        return informe

    analisis = _analizar(items, cfg)
    grupos, resumen_duplicados = detectar_duplicados(items, analisis, cfg)
    _log.info(
        "Duplicados: %s grupos, %s copias, %s recuperables",
        f"{resumen_duplicados.grupos:,}",
        f"{resumen_duplicados.duplicados:,}",
        f"{resumen_duplicados.bytes_recuperables:,} bytes",
    )

    plan = construir_plan(items, analisis, grupos, cfg)
    _log.info("Plan: %s movimientos, %s ya ordenados", f"{plan.total:,}", f"{plan.ya_ordenados:,}")

    csv_ruta = escribir_csv(plan, cfg.carpeta_trabajo / f"plan-{sello}.csv", cfg.raiz)
    manifiesto = _ruta_manifiesto(cfg, sello)

    if not cfg.simulacion and autorizar is not None and not autorizar(plan):
        _log.info("Ejecucion cancelada por la persona; el plan quedo en %s", csv_ruta)
        ejecucion = ResumenEjecucion(omitidos=plan.total)
        manifiesto = None
    else:
        ejecucion = ejecutar_plan(plan, cfg, manifiesto)

    informe = Informe(
        cfg=cfg,
        escaneo=resumen_escaneo,
        duplicados=resumen_duplicados,
        plan=plan,
        ejecucion=ejecucion,
        segundos=time.monotonic() - inicio,
        csv=csv_ruta,
        manifiesto=manifiesto if ejecucion.aplicados else None,
    )
    escribir_json(informe, cfg.carpeta_trabajo / f"resumen-{sello}.json")
    return informe


def aplicar_plan_existente(plan: Plan, cfg: Configuracion) -> Informe:
    """Aplica un plan ya calculado (leido de un CSV previo).

    Evita repetir el escaneo y el hashing sobre acervos grandes: el analisis
    caro se hace una vez en simulacion y despues solo se materializa.
    """
    inicio = time.monotonic()
    sello = marca_tiempo()
    manifiesto = _ruta_manifiesto(cfg, sello)
    ejecucion = ejecutar_plan(plan, cfg, manifiesto)
    informe = Informe(
        cfg=cfg,
        escaneo=ResumenEscaneo(aceptados=plan.total),
        duplicados=ResumenDuplicados(duplicados=plan.duplicados),
        plan=plan,
        ejecucion=ejecucion,
        segundos=time.monotonic() - inicio,
        csv=None,
        manifiesto=manifiesto if ejecucion.aplicados else None,
    )
    escribir_json(informe, cfg.carpeta_trabajo / f"resumen-{sello}.json")
    return informe


# --------------------------------------------------------------------------- #
# Fases internas
# --------------------------------------------------------------------------- #

def _recolectar(cfg: Configuracion, resumen: ResumenEscaneo) -> list[ArchivoEncontrado]:
    """Materializa el generador del escaneo mostrando progreso."""
    encontrados: list[ArchivoEncontrado] = []
    barra = Progreso("escaneando ", 0)
    try:
        for item in escanear(cfg, resumen):
            encontrados.append(item)
            barra.avanzar()
    finally:
        barra.cerrar()
    return encontrados


def _analizar(items: Sequence[ArchivoEncontrado], cfg: Configuracion) -> dict[Path, Analisis]:
    """Resuelve metadatos de todos los archivos en paralelo.

    Es la fase que paga el costo de disco: se hace en paralelo justamente por
    eso. La firma de imagen sale gratis de la misma apertura que la fecha, asi
    que se calcula siempre y no depende del modo de duplicados.
    """
    resultados = mapear(analizar, items, procesos=cfg.procesos, etiqueta="analizando ")
    return {info.ruta: info for info in resultados}


def _ruta_manifiesto(cfg: Configuracion, sello: str) -> Path | None:
    """Ruta del manifiesto, o `None` si no corresponde escribirlo."""
    if cfg.simulacion or not cfg.escribir_manifiesto:
        return None
    return cfg.carpeta_trabajo / f"manifiesto-{sello}.jsonl"


def _informe_vacio(cfg: Configuracion, escaneo: ResumenEscaneo, inicio: float) -> Informe:
    """Informe para el caso sin archivos elegibles."""
    return Informe(
        cfg=cfg,
        escaneo=escaneo,
        duplicados=ResumenDuplicados(),
        plan=Plan(),
        ejecucion=ResumenEjecucion(),
        segundos=time.monotonic() - inicio,
    )
