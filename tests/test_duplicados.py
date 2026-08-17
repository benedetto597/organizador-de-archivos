"""Pruebas de la deteccion de duplicados en sus dos niveles."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mediaorg.config import Configuracion, ModoDuplicados
from mediaorg.duplicados import MotivoDuplicado, detectar_duplicados
from mediaorg.escaneo import ArchivoEncontrado, escanear
from mediaorg.huellas import huella_de_bytes, huella_de_pixeles, leer_firma_imagen
from mediaorg.metadatos import analizar
from mediaorg.rutas_seguras import ruta_del_sistema

from .ayudas import crear_imagen, crear_imagen_con_metadatos


def _preparar(base: Path, modo: ModoDuplicados) -> tuple[list[ArchivoEncontrado], dict, Configuracion]:
    """Escanea y analiza un arbol de prueba en modo serial."""
    cfg = Configuracion(raiz=base, duplicados=modo, procesos=1, simulacion=True)
    items = list(escanear(cfg))
    analisis = {i.ruta: analizar(i) for i in items}
    return items, analisis, cfg


class Huellas(unittest.TestCase):
    """Propiedades basicas de las funciones de hash."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def test_bytes_iguales_misma_huella(self) -> None:
        a = crear_imagen(self.base / "a.png", (1, 2, 3))
        b = self.base / "b.png"
        b.write_bytes(a.read_bytes())
        self.assertEqual(
            huella_de_bytes(ruta_del_sistema(a)).huella,
            huella_de_bytes(ruta_del_sistema(b)).huella,
        )

    def test_bytes_distintos_huella_distinta(self) -> None:
        a = crear_imagen(self.base / "a.png", (1, 2, 3))
        b = crear_imagen(self.base / "b.png", (9, 9, 9))
        self.assertNotEqual(
            huella_de_bytes(ruta_del_sistema(a)).huella,
            huella_de_bytes(ruta_del_sistema(b)).huella,
        )

    def test_pixeles_iguales_pese_a_metadatos(self) -> None:
        a = crear_imagen(self.base / "a.png", (4, 5, 6))
        b = crear_imagen_con_metadatos(self.base / "b.png", (4, 5, 6))
        self.assertNotEqual(a.read_bytes(), b.read_bytes())
        self.assertEqual(
            huella_de_pixeles(ruta_del_sistema(a)).huella,
            huella_de_pixeles(ruta_del_sistema(b)).huella,
        )

    def test_error_se_reporta_sin_lanzar(self) -> None:
        resultado = huella_de_bytes(ruta_del_sistema(self.base / "no-existe.png"))
        self.assertIsNone(resultado.huella)
        self.assertIsNotNone(resultado.error)

    def test_firma_de_imagen(self) -> None:
        ruta = crear_imagen(self.base / "a.png", tamano=(20, 10))
        firma = leer_firma_imagen(ruta_del_sistema(ruta))
        self.assertIsNotNone(firma)
        assert firma is not None
        self.assertEqual((firma.ancho, firma.alto), (20, 10))
        self.assertFalse(firma.animada)

    def test_firma_de_no_imagen(self) -> None:
        ruta = self.base / "x.mp4"
        ruta.write_bytes(b"no soy imagen")
        self.assertIsNone(leer_firma_imagen(ruta_del_sistema(ruta)))


class DeteccionPorBytes(unittest.TestCase):
    """Modo `byte`: solo copias exactas."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def test_encuentra_copia_exacta(self) -> None:
        a = crear_imagen(self.base / "a.png", (1, 2, 3))
        (self.base / "copia.png").write_bytes(a.read_bytes())
        items, analisis, cfg = _preparar(self.base, ModoDuplicados.BYTE)
        grupos, resumen = detectar_duplicados(items, analisis, cfg)
        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0].motivo, MotivoDuplicado.BYTE)
        self.assertEqual(len(grupos[0].integrantes), 2)
        self.assertEqual(resumen.duplicados, 1)

    def test_no_agrupa_archivos_distintos(self) -> None:
        crear_imagen(self.base / "a.png", (1, 2, 3))
        crear_imagen(self.base / "b.png", (9, 8, 7))
        items, analisis, cfg = _preparar(self.base, ModoDuplicados.BYTE)
        grupos, _ = detectar_duplicados(items, analisis, cfg)
        self.assertEqual(grupos, [])

    def test_modo_byte_ignora_copias_de_pixel(self) -> None:
        crear_imagen(self.base / "a.png", (4, 5, 6))
        crear_imagen_con_metadatos(self.base / "b.png", (4, 5, 6))
        items, analisis, cfg = _preparar(self.base, ModoDuplicados.BYTE)
        grupos, _ = detectar_duplicados(items, analisis, cfg)
        self.assertEqual(grupos, [])

    def test_solo_se_hashea_lo_que_colisiona_en_tamano(self) -> None:
        """El filtro por tamano es la optimizacion clave en acervos grandes."""
        crear_imagen(self.base / "unica.png", (1, 1, 1), tamano=(40, 40))
        a = crear_imagen(self.base / "a.png", (2, 2, 2), tamano=(8, 8))
        (self.base / "b.png").write_bytes(a.read_bytes())
        items, analisis, cfg = _preparar(self.base, ModoDuplicados.BYTE)
        _, resumen = detectar_duplicados(items, analisis, cfg)
        self.assertEqual(resumen.candidatos_byte, 2)
        self.assertEqual(resumen.hasheados_byte, 2)


class DeteccionPorPixeles(unittest.TestCase):
    """Modo `pixel`: copias identicas aunque los bytes difieran."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def test_encuentra_copia_con_metadatos_distintos(self) -> None:
        crear_imagen(self.base / "a.png", (4, 5, 6))
        crear_imagen_con_metadatos(self.base / "b.png", (4, 5, 6))
        items, analisis, cfg = _preparar(self.base, ModoDuplicados.PIXEL)
        grupos, resumen = detectar_duplicados(items, analisis, cfg)
        self.assertEqual(len(grupos), 1)
        self.assertEqual(grupos[0].motivo, MotivoDuplicado.PIXEL)
        self.assertEqual(resumen.duplicados, 1)

    def test_resoluciones_distintas_no_se_decodifican(self) -> None:
        crear_imagen(self.base / "a.png", (4, 5, 6), tamano=(8, 6))
        crear_imagen(self.base / "b.png", (4, 5, 6), tamano=(9, 6))
        items, analisis, cfg = _preparar(self.base, ModoDuplicados.PIXEL)
        grupos, resumen = detectar_duplicados(items, analisis, cfg)
        self.assertEqual(grupos, [])
        self.assertEqual(resumen.candidatos_pixel, 0)

    def test_transitividad_entre_bytes_y_pixeles(self) -> None:
        """A=B por bytes y B=C por pixeles debe dar un solo grupo de tres."""
        a = crear_imagen(self.base / "a.png", (7, 7, 7))
        (self.base / "b.png").write_bytes(a.read_bytes())
        crear_imagen_con_metadatos(self.base / "c.png", (7, 7, 7))
        items, analisis, cfg = _preparar(self.base, ModoDuplicados.PIXEL)
        grupos, resumen = detectar_duplicados(items, analisis, cfg)
        self.assertEqual(len(grupos), 1)
        self.assertEqual(len(grupos[0].integrantes), 3)
        self.assertEqual(resumen.duplicados, 2)

    def test_no_decodifica_dos_veces_el_mismo_contenido(self) -> None:
        """Solo un representante por componente entra a la fase de pixeles."""
        a = crear_imagen(self.base / "a.png", (7, 7, 7))
        (self.base / "b.png").write_bytes(a.read_bytes())
        (self.base / "c.png").write_bytes(a.read_bytes())
        items, analisis, cfg = _preparar(self.base, ModoDuplicados.PIXEL)
        _, resumen = detectar_duplicados(items, analisis, cfg)
        self.assertEqual(resumen.candidatos_pixel, 0)

    def test_modo_ninguno_no_detecta_nada(self) -> None:
        a = crear_imagen(self.base / "a.png", (1, 2, 3))
        (self.base / "b.png").write_bytes(a.read_bytes())
        items, analisis, cfg = _preparar(self.base, ModoDuplicados.NINGUNO)
        grupos, resumen = detectar_duplicados(items, analisis, cfg)
        self.assertEqual(grupos, [])
        self.assertEqual(resumen.grupos, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
