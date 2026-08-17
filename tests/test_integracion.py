"""Pruebas de extremo a extremo sobre un arbol que imita el acervo real."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from mediaorg.aplicacion import organizar
from mediaorg.config import (
    CARPETA_BORRAR,
    CARPETA_TRABAJO,
    Configuracion,
    ModoDuplicados,
)
from mediaorg.ejecutor import deshacer
from mediaorg.reportes import leer_csv, texto_resumen

from .ayudas import crear_imagen, crear_imagen_con_metadatos, crear_jpeg_con_exif, crear_mp4


def _arbol_de_prueba(base: Path) -> None:
    """Reproduce en miniatura los patrones reales de la carpeta Imagenes."""
    crear_jpeg_con_exif(base / "20230726_124814.jpg", datetime(2023, 7, 26, 12, 48, 14))
    crear_imagen(base / "IMG-20190606-WA0044.jpg", (10, 20, 30))
    crear_imagen(base / "Screenshot_20240608_193933_WhatsApp.jpg", (40, 50, 60))
    crear_imagen(base / "FB_IMG_1704399976041.jpg", (70, 80, 90))
    crear_imagen(base / "1000035167.jpg", (11, 22, 33))
    crear_mp4(base / "VID-20221020-WA0039.mp4", None)
    crear_mp4(base / "20240912_233242.mp4", datetime(2024, 9, 12, 23, 32, 42))
    # Copia byte a byte y copia identica en pixeles del MISMO archivo: la
    # union es transitiva, asi que las tres forman un solo grupo por pixeles.
    origen = base / "IMG-20190606-WA0044.jpg"
    (base / "copia exacta.jpg").write_bytes(origen.read_bytes())
    crear_imagen_con_metadatos(base / "misma imagen otros metadatos.jpg", (10, 20, 30))
    # Grupo independiente que solo puede detectarse por bytes: en video no hay
    # comparacion de pixeles.
    (base / "copia de video.mp4").write_bytes((base / "VID-20221020-WA0039.mp4").read_bytes())
    # Archivo ajeno: debe quedar intacto.
    (base / "notas.txt").write_text("no soy media", encoding="utf-8")
    # Subcarpeta preexistente.
    crear_imagen(base / "vieja" / "20211003_162633.jpg", (99, 88, 77))
    # Subcarpeta que se llama igual que una carpeta de salida: debe escanearse,
    # porque las salidas se excluyen por ruta exacta y no por nombre.
    crear_imagen(base / "vieja" / "Fotos" / "20220215_101010.jpg", (12, 34, 56))


class CorridaCompleta(unittest.TestCase):
    """Simulacion, ejecucion, idempotencia y reversion."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)
        _arbol_de_prueba(self.base)

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def _cfg(self, **extra) -> Configuracion:
        parametros = {
            "raiz": self.base,
            "procesos": 1,
            "duplicados": ModoDuplicados.PIXEL,
            "simulacion": True,
        }
        parametros.update(extra)
        return Configuracion(**parametros)

    def test_simulacion_no_modifica_nada(self) -> None:
        antes = sorted(p.name for p in self.base.rglob("*") if p.is_file())
        informe = organizar(self._cfg())
        despues = sorted(
            p.name
            for p in self.base.rglob("*")
            if p.is_file() and CARPETA_TRABAJO not in p.parts
        )
        self.assertEqual(antes, despues)
        self.assertGreater(informe.plan.total, 0)
        self.assertEqual(informe.ejecucion.aplicados, 0)

    def test_simulacion_escribe_csv_utilizable(self) -> None:
        informe = organizar(self._cfg())
        self.assertIsNotNone(informe.csv)
        assert informe.csv is not None
        plan = leer_csv(informe.csv, self._cfg())
        self.assertEqual(plan.total, informe.plan.total)

    def test_ejecucion_construye_el_arbol(self) -> None:
        informe = organizar(self._cfg(simulacion=False))
        self.assertGreater(informe.ejecucion.aplicados, 0)
        self.assertEqual(informe.ejecucion.fallidos, 0)
        self.assertTrue((self.base / "Fotos").is_dir())
        self.assertTrue((self.base / "Videos").is_dir())
        self.assertTrue((self.base / CARPETA_BORRAR).is_dir())

    def test_clasificacion_esperada(self) -> None:
        organizar(self._cfg(simulacion=False))
        rutas = {
            p.name: p.relative_to(self.base).parts
            for p in self.base.rglob("*")
            if p.is_file()
        }
        self.assertEqual(rutas["20230726_124814.jpg"][:4], ("Fotos", "2023", "2023-07", "Camara"))
        self.assertEqual(
            rutas["Screenshot_20240608_193933_WhatsApp.jpg"][:4],
            ("Fotos", "2024", "2024-06", "Capturas"),
        )
        self.assertEqual(
            rutas["IMG-20190606-WA0044.jpg"][:4], ("Fotos", "2019", "2019-06", "WhatsApp")
        )
        self.assertEqual(
            rutas["VID-20221020-WA0039.mp4"][:4], ("Videos", "2022", "2022-10", "WhatsApp")
        )
        self.assertEqual(rutas["1000035167.jpg"][0], "Fotos")
        self.assertEqual(rutas["1000035167.jpg"][3], "Galeria")

    def test_duplicados_van_a_borrar_y_nada_se_borra(self) -> None:
        informe = organizar(self._cfg(simulacion=False))
        en_borrar = [p for p in (self.base / CARPETA_BORRAR).rglob("*") if p.is_file()]
        # 2 copias de la imagen (grupo unificado por transitividad) + 1 de video.
        self.assertEqual(len(en_borrar), 3)
        motivos = {p.relative_to(self.base / CARPETA_BORRAR).parts[0] for p in en_borrar}
        self.assertEqual(motivos, {"copia-exacta", "copia-pixel"})
        self.assertEqual(informe.plan.duplicados, 3)
        # Un original de cada grupo sigue vivo fuera de la cuarentena.
        self.assertTrue(
            any(
                p.name == "IMG-20190606-WA0044.jpg"
                for p in (self.base / "Fotos").rglob("*")
                if p.is_file()
            )
        )

    def test_archivo_ajeno_queda_intacto(self) -> None:
        organizar(self._cfg(simulacion=False))
        self.assertTrue((self.base / "notas.txt").is_file())

    def test_segunda_corrida_es_idempotente(self) -> None:
        organizar(self._cfg(simulacion=False))
        segundo = organizar(self._cfg(simulacion=False))
        self.assertEqual(segundo.plan.total, 0)
        self.assertEqual(segundo.ejecucion.aplicados, 0)

    def test_reversion_restaura_el_arbol_original(self) -> None:
        antes = sorted(str(p.relative_to(self.base)) for p in self.base.rglob("*") if p.is_file())
        informe = organizar(self._cfg(simulacion=False))
        self.assertIsNotNone(informe.manifiesto)
        assert informe.manifiesto is not None
        deshacer(informe.manifiesto, simulacion=False)
        despues = sorted(
            str(p.relative_to(self.base))
            for p in self.base.rglob("*")
            if p.is_file() and CARPETA_TRABAJO not in p.parts
        )
        self.assertEqual(antes, despues)

    def test_destino_separado(self) -> None:
        salida = self.base.parent / f"{self.base.name}-salida"
        try:
            informe = organizar(self._cfg(simulacion=False, destino=salida))
            self.assertGreater(informe.ejecucion.aplicados, 0)
            self.assertTrue((salida / "Fotos").is_dir())
            self.assertFalse((self.base / "Fotos").exists())
        finally:
            if salida.exists():
                import shutil

                shutil.rmtree(salida, ignore_errors=True)

    def test_resumen_se_puede_imprimir(self) -> None:
        informe = organizar(self._cfg())
        texto = texto_resumen(informe)
        self.assertIn("RESUMEN", texto)
        self.assertIn("SIMULACION", texto)

    def test_limite_acota_el_trabajo(self) -> None:
        informe = organizar(self._cfg(limite=3))
        self.assertLessEqual(informe.escaneo.aceptados, 3)

    def test_subcarpeta_homonima_de_una_salida_si_se_escanea(self) -> None:
        informe = organizar(self._cfg())
        origenes = {m.origen.name for m in informe.plan.movimientos}
        self.assertIn("20220215_101010.jpg", origenes)

    def test_las_rutas_del_plan_no_arrastran_prefijo_de_ruta_larga(self) -> None:
        """`\\\\?\\` en un origen contaminaria reportes y comparaciones."""
        informe = organizar(self._cfg())
        for movimiento in informe.plan.movimientos:
            with self.subTest(origen=movimiento.origen):
                self.assertNotIn("?", str(movimiento.origen))
                self.assertTrue(str(movimiento.origen).startswith(str(self.base)))

    def test_csv_guarda_rutas_relativas(self) -> None:
        import csv as _csv

        informe = organizar(self._cfg())
        assert informe.csv is not None
        with open(informe.csv, encoding="utf-8-sig", newline="") as manejador:
            filas = list(_csv.DictReader(manejador, delimiter=";"))
        self.assertTrue(filas)
        for fila in filas:
            with self.subTest(origen=fila["origen"]):
                self.assertFalse(Path(fila["origen"]).is_absolute())

    def test_segunda_corrida_reconoce_lo_ya_ordenado(self) -> None:
        """Con `--reprocesar` los archivos ya ubicados no generan movimiento."""
        organizar(self._cfg(simulacion=False))
        segundo = organizar(self._cfg(reprocesar=True))
        self.assertEqual(segundo.plan.total, 0)
        self.assertGreater(segundo.plan.ya_ordenados, 0)

    def test_carpeta_vacia_no_falla(self) -> None:
        vacia = self.base / "vacia"
        vacia.mkdir()
        informe = organizar(Configuracion(raiz=vacia, procesos=1))
        self.assertEqual(informe.plan.total, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
