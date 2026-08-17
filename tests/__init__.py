"""Pruebas del organizador. Solo usan `unittest` de la biblioteca estandar."""

from __future__ import annotations

import logging

# Varias pruebas ejercen a proposito rutas de fallo (destino ocupado, archivo
# inexistente). Los avisos correspondientes son el comportamiento esperado y no
# deben ensuciar la salida de la suite.
logging.getLogger("mediaorg").addHandler(logging.NullHandler())
logging.getLogger("mediaorg").setLevel(logging.CRITICAL)
logging.getLogger("mediaorg").propagate = False
