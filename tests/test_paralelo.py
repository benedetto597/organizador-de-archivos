"""Pruebas del ayudante de ejecucion paralela."""

from __future__ import annotations

import unittest

from mediaorg.paralelo import TOPE_TRABAJADORES, mapear, procesos_efectivos, tamano_de_trozo


def doblar(valor: int) -> int:
    """Funcion de nivel superior de modulo: requisito de `pickle` bajo spawn."""
    return valor * 2


def fallar_en_tres(valor: int) -> int:
    """Falla de forma determinista para probar la propagacion de errores."""
    if valor == 3:
        raise RuntimeError("fallo deliberado")
    return valor


class Mapear(unittest.TestCase):
    """Contrato de `mapear`: mismo orden, mismos resultados."""

    def test_modo_serial(self) -> None:
        salida = list(mapear(doblar, [1, 2, 3], procesos=1, etiqueta="t", mostrar_progreso=False))
        self.assertEqual(salida, [2, 4, 6])

    def test_secuencia_vacia(self) -> None:
        salida = list(mapear(doblar, [], procesos=4, etiqueta="t", mostrar_progreso=False))
        self.assertEqual(salida, [])

    def test_modo_paralelo_conserva_el_orden(self) -> None:
        entrada = list(range(40))
        salida = list(mapear(doblar, entrada, procesos=4, etiqueta="t", mostrar_progreso=False))
        self.assertEqual(salida, [v * 2 for v in entrada])

    def test_excepcion_del_trabajador_se_propaga(self) -> None:
        with self.assertRaises(RuntimeError):
            list(mapear(fallar_en_tres, [1, 2, 3], procesos=1, etiqueta="t", mostrar_progreso=False))


class Acotado(unittest.TestCase):
    """Los limites del sistema operativo no se deben poder rebasar."""

    def test_tope_de_trabajadores(self) -> None:
        self.assertLessEqual(procesos_efectivos(10_000), TOPE_TRABAJADORES)

    def test_valores_invalidos_pasan_a_la_validacion(self) -> None:
        """`--procesos 0` debe fallar de forma visible, no corregirse solo."""
        self.assertEqual(procesos_efectivos(0), 0)
        self.assertEqual(procesos_efectivos(-5), -5)

    def test_valor_razonable_se_respeta(self) -> None:
        self.assertEqual(procesos_efectivos(8), 8)

    def test_peticion_desmedida_no_crea_pool_invalido(self) -> None:
        """Un `procesos` absurdo desde la biblioteca no debe romper el pool."""
        salida = list(mapear(doblar, [1, 2], procesos=10_000, etiqueta="t", mostrar_progreso=False))
        self.assertEqual(salida, [2, 4])

    def test_tamano_de_trozo_en_rango(self) -> None:
        self.assertEqual(tamano_de_trozo(100, 1), 1)
        self.assertGreaterEqual(tamano_de_trozo(100_000, 8), 16)
        self.assertLessEqual(tamano_de_trozo(10_000_000, 8), 256)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
