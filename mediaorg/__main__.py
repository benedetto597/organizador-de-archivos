"""Permite `python -m mediaorg ...` como alternativa a `organizar.py`."""

from __future__ import annotations

import multiprocessing
import sys

from .cli import main

if __name__ == "__main__":
    # Requisito de Windows: el arranque `spawn` reimporta este modulo en cada
    # proceso hijo y sin esta llamada un ejecutable congelado se re-lanzaria.
    multiprocessing.freeze_support()
    sys.exit(main())
