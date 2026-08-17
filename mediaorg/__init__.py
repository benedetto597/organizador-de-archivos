"""mediaorg - organizador reutilizable de imagenes y videos.

Ordena un acervo audiovisual en carpetas por tipo, fecha y origen, y aisla las
copias identicas (byte a byte y pixel a pixel) en una carpeta `BORRAR` sin
eliminar nada.

Uso como biblioteca:

    from pathlib import Path
    from mediaorg import Configuracion, organizar

    cfg = Configuracion(raiz=Path(r"D:\\Fotos"), simulacion=True)
    informe = organizar(cfg)
    print(informe.plan.total)

Uso como programa:

    python organizar.py "D:\\Fotos" --ejecutar

Solo depende de Pillow (licencia MIT-CMU) y de la biblioteca estandar.
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = [
    "__version__",
    "Configuracion",
    "Jerarquia",
    "ModoDuplicados",
    "ModoOperacion",
    "organizar",
    "aplicar_plan_existente",
    "deshacer",
    "Informe",
    "texto_resumen",
    "ErrorOrganizador",
]


def __getattr__(nombre: str):
    """Importa perezosamente para que `import mediaorg` sea instantaneo.

    Evita que el simple hecho de importar el paquete arrastre Pillow y el
    modulo de multiprocessing cuando solo se necesita, por ejemplo, la version.
    """
    if nombre in {"Configuracion", "Jerarquia", "ModoDuplicados", "ModoOperacion"}:
        from . import config

        return getattr(config, nombre)
    if nombre in {"organizar", "aplicar_plan_existente"}:
        from . import aplicacion

        return getattr(aplicacion, nombre)
    if nombre == "deshacer":
        from .ejecutor import deshacer

        return deshacer
    if nombre in {"Informe", "texto_resumen"}:
        from . import reportes

        return getattr(reportes, nombre)
    if nombre == "ErrorOrganizador":
        from .errores import ErrorOrganizador

        return ErrorOrganizador
    raise AttributeError(f"module {__name__!r} no tiene atributo {nombre!r}")
