"""Interfaz de linea de comandos.

Contrato de codigos de salida:

* `0`  - todo bien (incluye simulacion).
* `1`  - la corrida termino pero hubo movimientos fallidos.
* `2`  - configuracion invalida o plan ilegible.
* `130`- interrumpido por la persona (Ctrl+C).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .aplicacion import Autorizacion, aplicar_plan_existente, organizar
from .config import (
    CARPETA_BORRAR,
    Configuracion,
    Jerarquia,
    JerarquiaArchivos,
    ModoContenido,
    ModoDuplicados,
    ModoOperacion,
)
from .ejecutor import deshacer
from .errores import ErrorOrganizador
from .paralelo import procesos_efectivos
from .planificador import Plan
from .registro import configurar_logging, formatear_bytes
from .reportes import leer_csv, marca_tiempo, texto_resumen

__all__ = ["construir_parser", "main"]

_log = logging.getLogger(__name__)

_EPILOGO = f"""
Ejemplos:

  # 1) Simulacion: calcula el plan y escribe el CSV, sin mover nada
  python organizar.py "D:\\Fotos"

  # 2) Aplicar exactamente el plan revisado, sin repetir el hashing
  python organizar.py "D:\\Fotos" --aplicar-plan "...\\plan-20260813-120000.csv" --ejecutar

  # 3) Corrida directa (recalcula y mueve)
  python organizar.py "D:\\Fotos" --ejecutar

  # 4) Otra carpeta, copiando en vez de mover, a un disco distinto
  python organizar.py "D:\\Fotos" --destino "E:\\Ordenado" --copiar --ejecutar

  # 5) Revertir todo lo de una corrida
  python organizar.py "D:\\Fotos" --deshacer "...\\manifiesto-20260813-120000.jsonl"

Los duplicados nunca se borran: se aislan en la carpeta {CARPETA_BORRAR}.
"""


def construir_parser() -> argparse.ArgumentParser:
    """Define todos los parametros del CLI."""
    parser = argparse.ArgumentParser(
        prog="organizar",
        description=(
            "Organiza imagenes y videos en carpetas por tipo, fecha y origen, "
            "y aisla las copias identicas en una carpeta aparte."
        ),
        epilog=_EPILOGO,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "ruta",
        nargs="?",
        help="Carpeta a organizar. Si se omite, se usa la carpeta actual.",
    )
    parser.add_argument("--ruta", dest="ruta_opcion", help="Forma explicita de indicar la carpeta.")
    parser.add_argument(
        "--destino",
        help="Raiz del arbol de salida. Por defecto la misma carpeta de origen (in-place).",
    )

    grupo_modo = parser.add_argument_group("modo de operacion")
    grupo_modo.add_argument(
        "--ejecutar",
        action="store_true",
        help="Aplica los cambios. Sin esta bandera solo se simula.",
    )
    grupo_modo.add_argument(
        "--copiar",
        action="store_true",
        help="Copia en lugar de mover (requiere espacio libre equivalente).",
    )
    grupo_modo.add_argument(
        "--forzar",
        action="store_true",
        help="No pedir confirmacion interactiva antes de aplicar.",
    )
    grupo_modo.add_argument(
        "--aplicar-plan",
        metavar="CSV",
        help="Usa un plan calculado en una corrida previa, sin recalcular. "
        "Requiere --ejecutar para aplicarlo; por si solo tambien simula.",
    )
    grupo_modo.add_argument(
        "--deshacer",
        metavar="JSONL",
        help="Revierte una corrida usando su manifiesto.",
    )

    grupo_contenido = parser.add_argument_group("que se organiza")
    grupo_contenido.add_argument(
        "--modo",
        type=ModoContenido,
        choices=list(ModoContenido),
        default=ModoContenido.MEDIA,
        help="media: fotos y videos, recorriendo todo el arbol. "
        "archivos: documentos, comprimidos, instaladores y demas, solo primer nivel. "
        "todo: ambos. Por defecto: %(default)s",
    )
    grupo_contenido.add_argument(
        "--profundidad",
        type=int,
        default=None,
        metavar="N",
        help="Niveles de subcarpeta a recorrer. 1 = solo archivos sueltos, "
        "0 = sin limite. Por defecto: sin limite en modo media, 1 en los demas.",
    )

    grupo_arbol = parser.add_argument_group("forma del arbol")
    grupo_arbol.add_argument(
        "--jerarquia",
        type=Jerarquia,
        choices=list(Jerarquia),
        default=Jerarquia.TIPO_FECHA_FUENTE,
        help="Orden de las carpetas. Por defecto: %(default)s",
    )
    grupo_arbol.add_argument(
        "--jerarquia-archivos",
        type=JerarquiaArchivos,
        choices=list(JerarquiaArchivos),
        default=JerarquiaArchivos.CATEGORIA_SUBTIPO_ANIO,
        help="Forma del arbol para lo que no es foto ni video. Por defecto: %(default)s",
    )
    grupo_arbol.add_argument(
        "--mes-con-nombre",
        action="store_true",
        help="Nombra los meses como 2024-05-Mayo en lugar de 2024-05.",
    )

    grupo_dup = parser.add_argument_group("duplicados")
    grupo_dup.add_argument(
        "--duplicados",
        type=ModoDuplicados,
        choices=list(ModoDuplicados),
        default=ModoDuplicados.PIXEL,
        help="Profundidad de la deteccion. Por defecto: %(default)s",
    )
    grupo_dup.add_argument(
        "--prefiltro",
        action="store_true",
        help="Prefiltro JPEG rapido (escalado DCT). Mas velocidad, exactitud algo menor.",
    )

    grupo_filtros = parser.add_argument_group("filtros del escaneo")
    grupo_filtros.add_argument(
        "--incluir-ext",
        nargs="+",
        default=(),
        metavar="EXT",
        help="Extensiones extra a tratar como media (ej: .raf .insv).",
    )
    grupo_filtros.add_argument(
        "--excluir",
        nargs="+",
        default=(),
        metavar="CARPETA",
        help="Nombres de carpeta a ignorar durante el escaneo.",
    )
    grupo_filtros.add_argument(
        "--limite", type=int, default=0, metavar="N", help="Procesar solo N archivos (pruebas)."
    )
    grupo_filtros.add_argument(
        "--tamano-minimo",
        type=int,
        default=1,
        metavar="BYTES",
        help="Ignorar archivos menores a este tamano. Por defecto: %(default)s",
    )
    grupo_filtros.add_argument(
        "--reprocesar",
        action="store_true",
        help="Incluir tambien las carpetas ya organizadas (re-ordenar todo).",
    )
    grupo_filtros.add_argument(
        "--seguir-enlaces",
        action="store_true",
        help="Recorrer enlaces simbolicos y junctions (desactivado por seguridad).",
    )

    grupo_otros = parser.add_argument_group("otros")
    grupo_otros.add_argument(
        "--procesos", type=int, default=None, metavar="N", help="Procesos paralelos."
    )
    grupo_otros.add_argument(
        "--sin-manifiesto",
        action="store_true",
        help="No escribir el manifiesto de reversion (no recomendado).",
    )
    grupo_otros.add_argument("--log", metavar="ARCHIVO", help="Ruta del archivo de log.")
    grupo_otros.add_argument("-v", "--verboso", action="store_true", help="Log detallado.")
    grupo_otros.add_argument("-q", "--silencioso", action="store_true", help="Solo errores.")
    grupo_otros.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _nivel_log(args: argparse.Namespace) -> int:
    """Traduce las banderas de verbosidad a un nivel de logging."""
    if args.silencioso:
        return logging.ERROR
    if args.verboso:
        return logging.DEBUG
    return logging.INFO


def _construir_configuracion(args: argparse.Namespace) -> Configuracion:
    """Convierte los argumentos en una `Configuracion` validada."""
    ruta = args.ruta_opcion or args.ruta or "."
    # `is not None` y no truthiness: `--procesos 0` es un error que debe llegar
    # a la validacion de `Configuracion`, no descartarse en silencio.
    procesos = procesos_efectivos(args.procesos) if args.procesos is not None else None
    parametros: dict[str, object] = {
        "raiz": Path(ruta),
        "destino": Path(args.destino) if args.destino else None,
        "operacion": ModoOperacion.COPIAR if args.copiar else ModoOperacion.MOVER,
        "simulacion": not args.ejecutar,
        "modo": args.modo,
        "profundidad": args.profundidad,
        "jerarquia": args.jerarquia,
        "jerarquia_archivos": args.jerarquia_archivos,
        "duplicados": args.duplicados,
        "mes_con_nombre": args.mes_con_nombre,
        "prefiltro_rapido": args.prefiltro,
        "reprocesar": args.reprocesar,
        "escribir_manifiesto": not args.sin_manifiesto,
        "extensiones_extra": frozenset(args.incluir_ext),
        "excluir": frozenset(args.excluir),
        "limite": args.limite,
        "tamano_minimo": args.tamano_minimo,
        "seguir_enlaces": args.seguir_enlaces,
    }
    if procesos is not None:
        parametros["procesos"] = procesos
    return Configuracion(**parametros)  # type: ignore[arg-type]


def _confirmar(cantidad: int, bytes_totales: int, cfg: Configuracion) -> bool:
    """Pide confirmacion explicita antes de tocar el disco.

    Si la entrada estandar no es interactiva (ejecucion programada, tuberia)
    se asume que la bandera `--ejecutar` ya es la autorizacion.
    """
    if not sys.stdin or not sys.stdin.isatty():
        return True
    verbo = "copiar" if cfg.operacion is ModoOperacion.COPIAR else "mover"
    print(
        f"\nSe van a {verbo} {cantidad:,} archivos ({formatear_bytes(bytes_totales)})"
        f"\n  desde : {cfg.raiz}"
        f"\n  hacia : {cfg.destino_efectivo}"
        f"\nLos duplicados quedaran en la carpeta {CARPETA_BORRAR} (no se borra nada)."
    )
    respuesta = input("Escribe ORDENAR para continuar: ").strip()
    return respuesta == "ORDENAR"


def _autorizador(cfg: Configuracion) -> Autorizacion:
    """Crea el callback que el pipeline invoca con el plan ya calculado."""

    def autorizar(plan: Plan) -> bool:
        if not plan.movimientos:
            return True
        return _confirmar(plan.total, plan.bytes_a_mover, cfg)

    return autorizar


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada del CLI."""
    parser = construir_parser()
    args = parser.parse_args(argv)

    try:
        cfg = _construir_configuracion(args)
    except ErrorOrganizador as exc:
        print(f"Error de configuracion: {exc}", file=sys.stderr)
        return 2

    archivo_log = Path(args.log) if args.log else cfg.carpeta_trabajo / f"log-{marca_tiempo()}.txt"
    configurar_logging(_nivel_log(args), archivo_log)
    _log.debug("Configuracion: %s", cfg)

    try:
        if args.deshacer:
            resumen = deshacer(Path(args.deshacer), simulacion=not args.ejecutar)
            etiqueta = "revertirian" if not args.ejecutar else "revirtieron"
            print(
                f"\nSe {etiqueta} {resumen.aplicados:,} archivos."
                f" Omitidos: {resumen.omitidos:,}. Fallidos: {resumen.fallidos:,}."
            )
            if not args.ejecutar:
                print("Agrega --ejecutar para aplicar la reversion.")
            return 1 if resumen.fallidos else 0

        if args.aplicar_plan:
            plan = leer_csv(Path(args.aplicar_plan), cfg)
            if not plan.movimientos:
                print("El plan no contiene movimientos aplicables.")
                return 0
            if not cfg.simulacion and not args.forzar:
                if not _confirmar(plan.total, plan.bytes_a_mover, cfg):
                    print("Cancelado.")
                    return 0
            informe = aplicar_plan_existente(plan, cfg)
        else:
            autorizar = None if args.forzar else _autorizador(cfg)
            informe = organizar(cfg, autorizar)
    except KeyboardInterrupt:
        print("\nInterrumpido. Lo ya movido quedo registrado en el manifiesto.", file=sys.stderr)
        return 130
    except ErrorOrganizador as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(texto_resumen(informe))
    return 1 if informe.ejecucion.fallidos else 0
