"""Regresiones de seguridad.

Cada prueba de este archivo corresponde a un defecto real encontrado en la
revision del codigo: entradas que provienen de un archivo (un CSV de plan, un
manifiesto de reversion) y que por tanto no estan bajo el control del programa.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path, PureWindowsPath

from mediaorg.config import Configuracion
from mediaorg.ejecutor import VERSION_MANIFIESTO, deshacer, ejecutar_plan
from mediaorg.errores import ErrorDeManifiesto
from mediaorg.planificador import MOTIVO_ORGANIZAR, Movimiento, Plan
from mediaorg.reportes import escribir_csv, leer_csv
from mediaorg.rutas_seguras import es_subruta, normalizar

_CABECERA = "origen;destino;carpeta;tipo;fuente;fecha;origen_fecha;motivo;tamano_bytes\n"


class ContencionDeRutas(unittest.TestCase):
    """`es_subruta` es la unica barrera de frontera del programa."""

    def test_puntos_dobles_no_burlan_la_contencion(self) -> None:
        raiz = PureWindowsPath(r"C:\medios")
        fuga = raiz / ".." / ".." / "Windows" / "evil.exe"
        self.assertFalse(es_subruta(fuga, raiz))

    def test_contenedora_con_puntos_dobles_se_rechaza(self) -> None:
        self.assertFalse(
            es_subruta(PureWindowsPath(r"C:\a\b"), PureWindowsPath(r"C:\a\..\a"))
        )

    def test_normalizar_colapsa_los_puntos(self) -> None:
        self.assertEqual(
            normalizar(Path(r"C:\medios\Fotos\..\..\otro")), Path(r"C:\otro")
        )


class PlanCsvHostil(unittest.TestCase):
    """`leer_csv` recibe un archivo que la persona puede abrir y editar."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)
        (self.base / "medios").mkdir()
        (self.base / "ajeno").mkdir()
        self.ajeno = self.base / "ajeno" / "privado.docx"
        self.ajeno.write_text("secreto", encoding="utf-8")
        self.cfg = Configuracion(raiz=self.base / "medios", procesos=1)
        self.csv = self.base / "plan.csv"

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def _escribir(self, origen: str, destino: str) -> None:
        self.csv.write_text(
            _CABECERA + f"{origen};{destino};x;foto;Otros;;sistema;organizado;10\n",
            encoding="utf-8",
        )

    def test_origen_fuera_de_la_raiz_se_descarta(self) -> None:
        self._escribir(str(self.ajeno), "Fotos/2024/2024-05/Otros/privado.docx")
        plan = leer_csv(self.csv, self.cfg)
        self.assertEqual(plan.movimientos, [])
        self.assertEqual(plan.descartados, 1)
        self.assertIn("origen fuera de la raiz", plan.advertencias[0])

    def test_destino_con_puntos_dobles_se_descarta(self) -> None:
        self._escribir("foto.jpg", r"Fotos\..\..\fugado\x.jpg")
        plan = leer_csv(self.csv, self.cfg)
        self.assertEqual(plan.movimientos, [])
        self.assertEqual(plan.descartados, 1)
        self.assertIn("fuera del arbol", plan.advertencias[0])

    def test_ruta_hibrida_de_windows_se_descarta(self) -> None:
        """`C:notas.txt` y `\\Windows\\x` no son absolutas ni relativas puras."""
        for hibrida in (r"C:notas.txt", r"\Windows\system32\x.jpg"):
            with self.subTest(hibrida=hibrida):
                self._escribir(hibrida, "Fotos/2024/x.jpg")
                plan = leer_csv(self.csv, self.cfg)
                self.assertEqual(plan.movimientos, [])
                self.assertEqual(plan.descartados, 1)

    def test_fila_legitima_si_se_acepta(self) -> None:
        self._escribir("foto.jpg", "Fotos/2024/2024-05/Otros/foto.jpg")
        plan = leer_csv(self.csv, self.cfg)
        self.assertEqual(plan.total, 1)
        self.assertEqual(plan.descartados, 0)


class FormulasEnElCsv(unittest.TestCase):
    """El CSV se abre en Excel: un nombre de archivo no debe ejecutarse."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)
        self.cfg = Configuracion(raiz=self.base, procesos=1)

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def _plan_con(self, nombre: str) -> Plan:
        movimiento = Movimiento(
            origen=self.base / nombre,
            destino=self.base / "Fotos" / "2024" / nombre,
            tipo="foto",
            fuente="Otros",
            fecha_iso=None,
            origen_fecha="sistema",
            motivo=MOTIVO_ORGANIZAR,
            tamano=10,
        )
        return Plan(movimientos=[movimiento])

    def test_nombre_con_formula_se_neutraliza(self) -> None:
        ruta = self.base / "plan.csv"
        escribir_csv(self._plan_con("=cmd|calc!A1.jpg"), ruta, self.base)
        contenido = ruta.read_text(encoding="utf-8-sig")
        self.assertIn("'=cmd|calc!A1.jpg", contenido)

    def test_el_viaje_de_ida_y_vuelta_es_exacto(self) -> None:
        """Neutralizar no puede corromper el plan que luego se aplica."""
        for nombre in ("=formula.jpg", "-y08c0r.jpg", "+suma.jpg", "@arroba.jpg", "normal.jpg"):
            with self.subTest(nombre=nombre):
                ruta = self.base / "plan.csv"
                escribir_csv(self._plan_con(nombre), ruta, self.base)
                plan = leer_csv(ruta, self.cfg)
                self.assertEqual(plan.total, 1)
                self.assertEqual(plan.movimientos[0].origen.name, nombre)


class ManifiestoHostil(unittest.TestCase):
    """El manifiesto vive en el disco de medios: es entrada no confiable."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)
        self.medios = self.base / "medios"
        self.medios.mkdir()
        self.ajeno = self.base / "ajeno"
        self.ajeno.mkdir()
        self.victima = self.ajeno / "importante.docx"
        self.victima.write_text("no me muevas", encoding="utf-8")
        self.manifiesto = self.base / "manifiesto.jsonl"

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def _escribir(self, operaciones: list[dict], con_encabezado: bool = True) -> None:
        lineas = []
        if con_encabezado:
            lineas.append(
                json.dumps(
                    {
                        "tipo": "encabezado",
                        "version": VERSION_MANIFIESTO,
                        "raiz": str(self.medios),
                        "destino": str(self.medios),
                        "operacion": "mover",
                        "jerarquia": "tipo-fecha-fuente",
                    }
                )
            )
        lineas += [json.dumps(op) for op in operaciones]
        self.manifiesto.write_text("\n".join(lineas) + "\n", encoding="utf-8")

    def test_operacion_fuera_del_arbol_declarado_no_se_revierte(self) -> None:
        self._escribir(
            [
                {
                    "tipo": "operacion",
                    "op": "mover",
                    "origen": str(self.ajeno / "robado" / "importante.docx"),
                    "destino": str(self.victima),
                    "motivo": "organizado",
                    "tamano": 10,
                }
            ]
        )
        with self.assertRaises(ErrorDeManifiesto):
            deshacer(self.manifiesto, simulacion=False)
        self.assertTrue(self.victima.is_file())

    def test_manifiesto_sin_encabezado_se_rechaza(self) -> None:
        self._escribir(
            [
                {
                    "tipo": "operacion",
                    "op": "mover",
                    "origen": str(self.medios / "a.jpg"),
                    "destino": str(self.medios / "Fotos" / "a.jpg"),
                }
            ],
            con_encabezado=False,
        )
        with self.assertRaises(ErrorDeManifiesto):
            deshacer(self.manifiesto, simulacion=False)

    def test_operacion_legitima_si_se_revierte(self) -> None:
        origen = self.medios / "a.jpg"
        destino = self.medios / "Fotos" / "2024" / "a.jpg"
        destino.parent.mkdir(parents=True)
        destino.write_bytes(b"contenido")
        self._escribir(
            [
                {
                    "tipo": "operacion",
                    "op": "mover",
                    "origen": str(origen),
                    "destino": str(destino),
                    "tamano": 9,
                }
            ]
        )
        resumen = deshacer(self.manifiesto, simulacion=False)
        self.assertEqual(resumen.aplicados, 1)
        self.assertTrue(origen.is_file())

    def test_mezcla_de_operaciones_solo_revierte_las_validas(self) -> None:
        origen = self.medios / "b.jpg"
        destino = self.medios / "Fotos" / "b.jpg"
        destino.parent.mkdir(parents=True)
        destino.write_bytes(b"ok")
        self._escribir(
            [
                {
                    "tipo": "operacion",
                    "op": "mover",
                    "origen": str(self.ajeno / "x.docx"),
                    "destino": str(self.victima),
                },
                {"tipo": "operacion", "op": "mover", "origen": str(origen), "destino": str(destino)},
            ]
        )
        resumen = deshacer(self.manifiesto, simulacion=False)
        self.assertEqual(resumen.aplicados, 1)
        self.assertEqual(resumen.omitidos, 1)
        self.assertTrue(self.victima.is_file())
        self.assertTrue(origen.is_file())

    def test_deshacer_simula_por_defecto(self) -> None:
        """El valor por defecto no debe tocar el disco."""
        origen = self.medios / "c.jpg"
        destino = self.medios / "Fotos" / "c.jpg"
        destino.parent.mkdir(parents=True)
        destino.write_bytes(b"ok")
        self._escribir(
            [{"tipo": "operacion", "op": "mover", "origen": str(origen), "destino": str(destino)}]
        )
        deshacer(self.manifiesto)
        self.assertTrue(destino.is_file())
        self.assertFalse(origen.exists())


class CarpetasIntocablesEnElRecorrido(unittest.TestCase):
    """El control por nombre debe aplicarse en CADA descenso, no solo en la raiz.

    Regresion de un defecto real: `NOMBRES_SISTEMA_PROHIBIDOS` solo se
    comprobaba sobre la carpeta de inicio, asi que organizar una carpeta de
    perfil movia imagenes desde `AppData` y desarmaba `site-packages`,
    `node_modules` y `__pycache__`, ninguno de los cuales tiene marcador propio.
    """

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)
        # Reproduce el arbol del escenario verificado.
        self._crear("AppData/Roaming/Teams/avatars/yo.jpg")
        self._crear("AppData/Local/Google/Chrome/cache/img.png")
        self._crear("venv/Lib/site-packages/paquete/core.py")
        self._crear("proyecto/node_modules/.package-lock.json")
        self._crear("proyecto/node_modules/express/index.js")
        self._crear("proyecto/__pycache__/modulo.cpython-313.pyc")
        self._crear(".git/objects/ab/deadbeef")
        # Un archivo legitimo que SI debe organizarse.
        self._crear("foto-real.jpg")

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def _crear(self, relativa: str) -> Path:
        ruta = self.base / relativa
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(b"contenido de prueba")
        return ruta

    def _rutas_del_plan(self, **extra) -> set[str]:
        from mediaorg.aplicacion import organizar
        from mediaorg.config import ModoContenido

        parametros = {
            "raiz": self.base,
            "modo": ModoContenido.TODO,
            "profundidad": 0,  # sin limite: el peor caso
            "procesos": 1,
            "simulacion": True,
        }
        parametros.update(extra)
        informe = organizar(Configuracion(**parametros))
        return {str(m.origen.relative_to(self.base)) for m in informe.plan.movimientos}

    def test_no_toca_nada_dentro_de_appdata(self) -> None:
        origenes = self._rutas_del_plan()
        intrusos = [o for o in origenes if o.lower().startswith("appdata")]
        self.assertEqual(intrusos, [], f"se planifico mover datos de perfil: {intrusos}")

    def test_no_desarma_entornos_ni_dependencias(self) -> None:
        origenes = self._rutas_del_plan()
        marcas = ("site-packages", "node_modules", "__pycache__", ".git")
        intrusos = [o for o in origenes if any(m in o.lower() for m in marcas)]
        self.assertEqual(intrusos, [], f"se planifico desarmar un arbol de herramientas: {intrusos}")

    def test_si_organiza_lo_que_es_del_usuario(self) -> None:
        """La proteccion no puede volverse tan amplia que no organice nada."""
        self.assertIn("foto-real.jpg", self._rutas_del_plan())

    def test_lo_omitido_se_reporta_y_no_se_anuncia_como_intacto_cero(self) -> None:
        from mediaorg.config import ModoContenido
        from mediaorg.escaneo import ResumenEscaneo, escanear

        cfg = Configuracion(
            raiz=self.base, modo=ModoContenido.TODO, profundidad=0, procesos=1
        )
        resumen = ResumenEscaneo()
        list(escanear(cfg, resumen))
        self.assertGreater(resumen.carpetas_de_sistema_omitidas, 0)


class FallosAisladosPorArchivo(unittest.TestCase):
    """Un archivo problematico no puede llevarse la corrida completa."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def test_carpeta_destino_imposible_no_aborta_el_resto(self) -> None:
        """Crear una carpeta sobre un archivo lanza `RutaInsegura`, no `OSError`."""
        estorbo = self.base / "estorbo"
        estorbo.write_bytes(b"soy un archivo, no una carpeta")
        bueno = self.base / "bueno.jpg"
        bueno.write_bytes(b"abc")

        plan = Plan(
            movimientos=[
                Movimiento(
                    origen=self.base / "otro.jpg",
                    destino=estorbo / "imposible" / "otro.jpg",
                    tipo="foto",
                    fuente="Otros",
                    fecha_iso=None,
                    origen_fecha="sistema",
                    motivo=MOTIVO_ORGANIZAR,
                    tamano=1,
                ),
                Movimiento(
                    origen=bueno,
                    destino=self.base / "Fotos" / "bueno.jpg",
                    tipo="foto",
                    fuente="Otros",
                    fecha_iso=None,
                    origen_fecha="sistema",
                    motivo=MOTIVO_ORGANIZAR,
                    tamano=3,
                ),
            ]
        )
        cfg = Configuracion(raiz=self.base, simulacion=False, procesos=1)
        resumen = ejecutar_plan(plan, cfg)
        self.assertEqual(resumen.fallidos, 1)
        self.assertEqual(resumen.aplicados, 1)
        self.assertTrue((self.base / "Fotos" / "bueno.jpg").is_file())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
