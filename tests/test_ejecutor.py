"""Pruebas de la ejecucion, el manifiesto y la reversion."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mediaorg.config import Configuracion, ModoOperacion
from mediaorg.ejecutor import VERSION_MANIFIESTO, deshacer, ejecutar_plan
from mediaorg.errores import ErrorDeManifiesto
from mediaorg.planificador import MOTIVO_ORGANIZAR, Movimiento, Plan


def _movimiento(origen: Path, destino: Path, tamano: int = 3) -> Movimiento:
    return Movimiento(
        origen=origen,
        destino=destino,
        tipo="foto",
        fuente="Camara",
        fecha_iso="2024-05-12T10:00:00",
        origen_fecha="nombre",
        motivo=MOTIVO_ORGANIZAR,
        tamano=tamano,
    )


class Ejecucion(unittest.TestCase):
    """Movimientos reales sobre un arbol temporal."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)
        self.origen = self.base / "a.jpg"
        self.origen.write_bytes(b"abc")
        self.destino = self.base / "Fotos" / "2024" / "a.jpg"

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def _cfg(self, **extra) -> Configuracion:
        parametros = {"raiz": self.base, "simulacion": False, "procesos": 1}
        parametros.update(extra)
        return Configuracion(**parametros)

    def test_mueve_y_crea_carpetas(self) -> None:
        plan = Plan(movimientos=[_movimiento(self.origen, self.destino)])
        resumen = ejecutar_plan(plan, self._cfg())
        self.assertEqual(resumen.aplicados, 1)
        self.assertTrue(self.destino.is_file())
        self.assertFalse(self.origen.exists())
        self.assertEqual(self.destino.read_bytes(), b"abc")

    def test_simulacion_no_toca_el_disco(self) -> None:
        plan = Plan(movimientos=[_movimiento(self.origen, self.destino)])
        resumen = ejecutar_plan(plan, self._cfg(simulacion=True))
        self.assertEqual(resumen.aplicados, 0)
        self.assertEqual(resumen.omitidos, 1)
        self.assertTrue(self.origen.is_file())
        self.assertFalse(self.destino.exists())

    def test_copiar_deja_el_original(self) -> None:
        plan = Plan(movimientos=[_movimiento(self.origen, self.destino)])
        resumen = ejecutar_plan(plan, self._cfg(operacion=ModoOperacion.COPIAR))
        self.assertEqual(resumen.aplicados, 1)
        self.assertTrue(self.origen.is_file())
        self.assertTrue(self.destino.is_file())

    def test_nunca_sobreescribe_el_destino(self) -> None:
        self.destino.parent.mkdir(parents=True)
        self.destino.write_bytes(b"NO PISAR")
        plan = Plan(movimientos=[_movimiento(self.origen, self.destino)])
        resumen = ejecutar_plan(plan, self._cfg())
        self.assertEqual(resumen.aplicados, 0)
        self.assertEqual(resumen.fallidos, 1)
        self.assertEqual(self.destino.read_bytes(), b"NO PISAR")
        self.assertTrue(self.origen.is_file())

    def test_un_fallo_no_detiene_el_resto(self) -> None:
        otro = self.base / "b.jpg"
        otro.write_bytes(b"xyz")
        faltante = self.base / "no-existe.jpg"
        plan = Plan(
            movimientos=[
                _movimiento(faltante, self.base / "Fotos" / "z.jpg"),
                _movimiento(otro, self.base / "Fotos" / "b.jpg"),
            ]
        )
        resumen = ejecutar_plan(plan, self._cfg())
        self.assertEqual(resumen.aplicados, 1)
        self.assertEqual(resumen.fallidos, 1)
        self.assertTrue((self.base / "Fotos" / "b.jpg").is_file())

    def test_plan_vacio(self) -> None:
        resumen = ejecutar_plan(Plan(), self._cfg())
        self.assertEqual(resumen.aplicados, 0)
        self.assertEqual(resumen.fallidos, 0)


class Manifiesto(unittest.TestCase):
    """Registro de operaciones y reversion."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)
        self.origen = self.base / "a.jpg"
        self.origen.write_bytes(b"abc")
        self.destino = self.base / "Fotos" / "2024" / "a.jpg"
        self.manifiesto = self.base / "_ORGANIZADOR" / "manifiesto.jsonl"
        self.cfg = Configuracion(raiz=self.base, simulacion=False, procesos=1)

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def _ejecutar(self) -> None:
        plan = Plan(movimientos=[_movimiento(self.origen, self.destino)])
        ejecutar_plan(plan, self.cfg, self.manifiesto)

    def test_escribe_encabezado_y_operacion(self) -> None:
        self._ejecutar()
        lineas = [json.loads(l) for l in self.manifiesto.read_text("utf-8").splitlines() if l]
        self.assertEqual(lineas[0]["tipo"], "encabezado")
        self.assertEqual(lineas[0]["version"], VERSION_MANIFIESTO)
        self.assertEqual(lineas[1]["tipo"], "operacion")
        self.assertEqual(Path(lineas[1]["destino"]), self.destino)

    def test_reversion_devuelve_el_archivo(self) -> None:
        self._ejecutar()
        resumen = deshacer(self.manifiesto, simulacion=False)
        self.assertEqual(resumen.aplicados, 1)
        self.assertTrue(self.origen.is_file())
        self.assertFalse(self.destino.exists())

    def test_reversion_simula_por_defecto(self) -> None:
        """El valor por defecto de `deshacer` no toca el disco."""
        self._ejecutar()
        resumen = deshacer(self.manifiesto)
        self.assertEqual(resumen.aplicados, 1)
        self.assertTrue(self.destino.is_file())
        self.assertFalse(self.origen.exists())

    def test_reversion_omite_si_el_origen_esta_ocupado(self) -> None:
        self._ejecutar()
        self.origen.write_bytes(b"otro contenido")
        resumen = deshacer(self.manifiesto, simulacion=False)
        self.assertEqual(resumen.aplicados, 0)
        self.assertEqual(resumen.omitidos, 1)
        self.assertEqual(self.origen.read_bytes(), b"otro contenido")
        self.assertTrue(self.destino.is_file())

    def test_reversion_poda_carpetas_vacias(self) -> None:
        self._ejecutar()
        deshacer(self.manifiesto, simulacion=False)
        self.assertFalse((self.base / "Fotos" / "2024").exists())

    def test_manifiesto_inexistente(self) -> None:
        with self.assertRaises(ErrorDeManifiesto):
            deshacer(self.base / "no-existe.jsonl")

    def test_manifiesto_corrupto(self) -> None:
        malo = self.base / "malo.jsonl"
        malo.write_text("{esto no es json}\n", encoding="utf-8")
        with self.assertRaises(ErrorDeManifiesto):
            deshacer(malo)

    def test_version_futura_se_rechaza(self) -> None:
        futuro = self.base / "futuro.jsonl"
        futuro.write_text(
            json.dumps({"tipo": "encabezado", "version": VERSION_MANIFIESTO + 1}) + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(ErrorDeManifiesto):
            deshacer(futuro)

    def test_manifiesto_sin_operaciones(self) -> None:
        vacio = self.base / "vacio.jsonl"
        vacio.write_text(
            json.dumps({"tipo": "encabezado", "version": VERSION_MANIFIESTO}) + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(ErrorDeManifiesto):
            deshacer(vacio)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
