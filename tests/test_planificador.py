"""Pruebas de la politica de destinos y de eleccion de superviviente."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from mediaorg.config import CARPETA_BORRAR, CARPETA_SIN_FECHA, Configuracion, Jerarquia
from mediaorg.duplicados import GrupoDuplicados, MotivoDuplicado
from mediaorg.escaneo import ArchivoEncontrado
from mediaorg.metadatos import Analisis
from mediaorg.planificador import MOTIVO_ORGANIZAR, construir_plan
from mediaorg.tipos import TipoMedia


def _item(base: Path, nombre: str, tamano: int = 100, mtime: float = 1000.0) -> ArchivoEncontrado:
    return ArchivoEncontrado(
        ruta=base / nombre, tamano=tamano, mtime=mtime, tipo=TipoMedia.FOTO
    )


def _analisis(
    item: ArchivoEncontrado,
    fecha: datetime | None = datetime(2024, 5, 12, 10, 0, 0),
    fuente: str = "Camara",
    origen: str = "nombre",
    tipo: TipoMedia = TipoMedia.FOTO,
) -> Analisis:
    return Analisis(
        ruta=item.ruta,
        fecha_iso=fecha.isoformat(timespec="seconds") if fecha else None,
        origen_fecha=origen,
        fuente=fuente,
        firma=None,
        tipo_efectivo=tipo.value,
    )


class Jerarquias(unittest.TestCase):
    """Cada jerarquia produce exactamente los segmentos esperados."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def _plan_con(self, jerarquia: Jerarquia):
        cfg = Configuracion(raiz=self.base, jerarquia=jerarquia, procesos=1)
        item = _item(self.base, "20240512_100000.jpg")
        return construir_plan([item], {item.ruta: _analisis(item)}, [], cfg)

    def test_tipo_fecha_fuente(self) -> None:
        plan = self._plan_con(Jerarquia.TIPO_FECHA_FUENTE)
        relativa = plan.movimientos[0].destino.relative_to(self.base)
        self.assertEqual(relativa.parts[:4], ("Fotos", "2024", "2024-05", "Camara"))

    def test_tipo_fecha(self) -> None:
        plan = self._plan_con(Jerarquia.TIPO_FECHA)
        relativa = plan.movimientos[0].destino.relative_to(self.base)
        self.assertEqual(relativa.parts[:3], ("Fotos", "2024", "2024-05"))

    def test_tipo_fuente(self) -> None:
        plan = self._plan_con(Jerarquia.TIPO_FUENTE)
        relativa = plan.movimientos[0].destino.relative_to(self.base)
        self.assertEqual(relativa.parts[:2], ("Fotos", "Camara"))

    def test_solo_fecha(self) -> None:
        plan = self._plan_con(Jerarquia.FECHA)
        relativa = plan.movimientos[0].destino.relative_to(self.base)
        self.assertEqual(relativa.parts[:2], ("2024", "2024-05"))

    def test_solo_tipo(self) -> None:
        plan = self._plan_con(Jerarquia.TIPO)
        relativa = plan.movimientos[0].destino.relative_to(self.base)
        self.assertEqual(relativa.parts[:1], ("Fotos",))

    def test_sin_fecha_usa_carpeta_propia(self) -> None:
        cfg = Configuracion(raiz=self.base, procesos=1)
        item = _item(self.base, "1000035167.jpg")
        plan = construir_plan([item], {item.ruta: _analisis(item, fecha=None)}, [], cfg)
        relativa = plan.movimientos[0].destino.relative_to(self.base)
        self.assertEqual(relativa.parts[:2], ("Fotos", CARPETA_SIN_FECHA))

    def test_video_va_a_su_propia_raiz(self) -> None:
        cfg = Configuracion(raiz=self.base, procesos=1)
        item = ArchivoEncontrado(self.base / "v.mp4", 10, 1.0, TipoMedia.VIDEO)
        info = _analisis(item, tipo=TipoMedia.VIDEO)
        plan = construir_plan([item], {item.ruta: info}, [], cfg)
        self.assertEqual(plan.movimientos[0].destino.relative_to(self.base).parts[0], "Videos")

    def test_destino_siempre_dentro_del_arbol(self) -> None:
        cfg = Configuracion(raiz=self.base, procesos=1)
        item = _item(self.base, "x.jpg")
        info = _analisis(item, fuente="../../fuera")
        plan = construir_plan([item], {item.ruta: info}, [], cfg)
        destino = plan.movimientos[0].destino
        self.assertTrue(str(destino).startswith(str(self.base)))
        self.assertNotIn("..", destino.parts)


class Duplicados(unittest.TestCase):
    """Politica de superviviente y ruta de cuarentena."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)
        self.cfg = Configuracion(raiz=self.base, procesos=1)

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def test_copias_van_a_borrar(self) -> None:
        a = _item(self.base, "a.jpg", tamano=500)
        b = _item(self.base, "b.jpg", tamano=500)
        analisis = {a.ruta: _analisis(a), b.ruta: _analisis(b)}
        grupo = GrupoDuplicados(MotivoDuplicado.BYTE, (a, b))
        plan = construir_plan([a, b], analisis, [grupo], self.cfg)

        destinos = {m.origen.name: m.destino.relative_to(self.base).parts for m in plan.movimientos}
        rutas_borrar = [p for p in destinos.values() if p[0] == CARPETA_BORRAR]
        self.assertEqual(len(rutas_borrar), 1)
        self.assertEqual(rutas_borrar[0][:3], (CARPETA_BORRAR, "copia-exacta", "Fotos"))
        self.assertEqual(plan.duplicados, 1)

    def test_sobrevive_el_de_fecha_confiable(self) -> None:
        confiable = _item(self.base, "20240512_100000.jpg", tamano=100)
        sin_fecha = _item(self.base, "1000035167.jpg", tamano=100)
        analisis = {
            confiable.ruta: _analisis(confiable, origen="exif"),
            sin_fecha.ruta: _analisis(sin_fecha, origen="sistema"),
        }
        grupo = GrupoDuplicados(MotivoDuplicado.PIXEL, (confiable, sin_fecha))
        plan = construir_plan([confiable, sin_fecha], analisis, [grupo], self.cfg)

        aislado = [m for m in plan.movimientos if m.es_duplicado]
        self.assertEqual(len(aislado), 1)
        self.assertEqual(aislado[0].origen.name, "1000035167.jpg")

    def test_a_igual_confianza_sobrevive_el_mas_grande(self) -> None:
        grande = _item(self.base, "grande.jpg", tamano=900)
        chico = _item(self.base, "chico.jpg", tamano=100)
        analisis = {grande.ruta: _analisis(grande), chico.ruta: _analisis(chico)}
        grupo = GrupoDuplicados(MotivoDuplicado.BYTE, (grande, chico))
        plan = construir_plan([grande, chico], analisis, [grupo], self.cfg)
        aislado = [m for m in plan.movimientos if m.es_duplicado]
        self.assertEqual(aislado[0].origen.name, "chico.jpg")

    def test_es_determinista(self) -> None:
        a = _item(self.base, "a.jpg")
        b = _item(self.base, "b.jpg")
        analisis = {a.ruta: _analisis(a), b.ruta: _analisis(b)}
        grupo = GrupoDuplicados(MotivoDuplicado.BYTE, (a, b))
        primero = construir_plan([a, b], analisis, [grupo], self.cfg)
        segundo = construir_plan([a, b], analisis, [grupo], self.cfg)
        self.assertEqual(
            [(m.origen, m.destino) for m in primero.movimientos],
            [(m.origen, m.destino) for m in segundo.movimientos],
        )


class Colisiones(unittest.TestCase):
    """Dos archivos distintos con el mismo nombre no se pisan."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)
        self.cfg = Configuracion(raiz=self.base, procesos=1)

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def test_mismo_nombre_en_carpetas_distintas(self) -> None:
        a = _item(self.base / "uno", "foto.jpg")
        b = _item(self.base / "dos", "foto.jpg")
        analisis = {a.ruta: _analisis(a), b.ruta: _analisis(b)}
        plan = construir_plan([a, b], analisis, [], self.cfg)
        destinos = {m.destino for m in plan.movimientos}
        self.assertEqual(len(destinos), 2)
        self.assertIn("foto~1.jpg", {d.name for d in destinos})

    def test_archivo_ya_en_su_destino_no_se_mueve(self) -> None:
        destino = self.base / "Fotos" / "2024" / "2024-05" / "Camara"
        destino.mkdir(parents=True)
        item = _item(destino, "20240512_100000.jpg")
        plan = construir_plan([item], {item.ruta: _analisis(item)}, [], self.cfg)
        self.assertEqual(plan.movimientos, [])
        self.assertEqual(plan.ya_ordenados, 1)

    def test_motivo_por_defecto_es_organizado(self) -> None:
        item = _item(self.base, "x.jpg")
        plan = construir_plan([item], {item.ruta: _analisis(item)}, [], self.cfg)
        self.assertEqual(plan.movimientos[0].motivo, MOTIVO_ORGANIZAR)
        self.assertFalse(plan.movimientos[0].es_duplicado)

    def test_item_sin_analisis_se_descarta(self) -> None:
        item = _item(self.base, "x.jpg")
        plan = construir_plan([item], {}, [], self.cfg)
        self.assertEqual(plan.movimientos, [])
        self.assertEqual(plan.descartados, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
