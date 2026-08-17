#!/usr/bin/env python3
"""Punto de entrada del organizador de imagenes y videos.

Ejecutar siempre a traves de este archivo (o de `python -m mediaorg`):

    python organizar.py "D:\\Fotos"              # simulacion
    python organizar.py "D:\\Fotos" --ejecutar   # aplica
    python organizar.py --help                                # todas las opciones

El guardado `if __name__ == "__main__"` no es decorativo: en Windows el
arranque de procesos es `spawn`, cada hijo reimporta este modulo y sin el
guardado la corrida se relanzaria en bucle.
"""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

# Permite ejecutar el script desde cualquier directorio de trabajo sin
# instalar el paquete.
_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.insert(0, str(_AQUI))

from mediaorg.cli import main  # noqa: E402  (import tras ajustar sys.path)

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
