"""Ejecuta los ejemplos de las docstrings como pruebas.

Un ejemplo en una docstring es documentacion que puede quedar desactualizada en
silencio. Incluirlo en la suite convierte cada ejemplo en una prueba real.
"""

from __future__ import annotations

import doctest
import unittest

from mediaorg import fechas, fuentes, registro, rutas_seguras

_MODULOS = (fechas, fuentes, registro, rutas_seguras)


def load_tests(
    loader: unittest.TestLoader, tests: unittest.TestSuite, ignore: str | None
) -> unittest.TestSuite:
    """Gancho estandar de `unittest` para agregar suites generadas."""
    for modulo in _MODULOS:
        tests.addTests(doctest.DocTestSuite(modulo))
    return tests


class HayEjemplos(unittest.TestCase):
    """Verifica que los modulos publicos documentados sigan trayendo ejemplos."""

    def test_modulos_clave_tienen_doctests(self) -> None:
        for modulo in (fechas, fuentes, rutas_seguras):
            with self.subTest(modulo=modulo.__name__):
                encontrados = doctest.DocTestFinder().find(modulo)
                self.assertTrue(
                    any(prueba.examples for prueba in encontrados),
                    f"{modulo.__name__} no tiene ningun ejemplo ejecutable",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
