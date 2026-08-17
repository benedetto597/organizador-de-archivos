"""Pruebas de la organizacion de archivos que no son foto ni video."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

from mediaorg.aplicacion import organizar
from mediaorg.categorias import Categoria, clasificar_archivo, extensiones_registradas
from mediaorg.config import (
    CARPETA_SIN_FECHA,
    Configuracion,
    JerarquiaArchivos,
    ModoContenido,
    ModoDuplicados,
)
from mediaorg.documentos import (
    fecha_desde_documento,
    fecha_desde_ooxml,
    fecha_desde_pdf,
)
from mediaorg.errores import ConfiguracionInvalida
from mediaorg.escaneo import ResumenEscaneo, escanear, marcador_de_unidad
from mediaorg.fechas import OrigenFecha
from mediaorg.metadatos import analizar
from mediaorg.tipos import EXTENSIONES_CONOCIDAS

from .ayudas import crear_archivo, crear_imagen


def _docx(ruta: Path, creado: str) -> Path:
    """Escribe un OOXML minimo con `dcterms:created`."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    nucleo = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
        ' xmlns:dcterms="http://purl.org/dc/terms/">'
        f"<dcterms:created>{creado}</dcterms:created>"
        "</cp:coreProperties>"
    )
    with zipfile.ZipFile(ruta, "w") as paquete:
        paquete.writestr("[Content_Types].xml", "<Types/>")
        paquete.writestr("docProps/core.xml", nucleo)
        paquete.writestr("word/document.xml", "<document/>")
    return ruta


def _odt(ruta: Path, creado: str) -> Path:
    """Escribe un OpenDocument minimo con `meta:creation-date`."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    meta = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
        ' xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0">'
        f"<office:meta><meta:creation-date>{creado}</meta:creation-date></office:meta>"
        "</office:document-meta>"
    )
    with zipfile.ZipFile(ruta, "w") as paquete:
        paquete.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        paquete.writestr("meta.xml", meta)
    return ruta


def _pdf(ruta: Path, creado: str) -> Path:
    """PDF minimo con `/CreationDate` en el diccionario de informacion."""
    cuerpo = (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        b"trailer\n<< /Info 2 0 R >>\n"
        b"2 0 obj\n<< /Producer (prueba) /CreationDate (D:" + creado.encode() + b") >>\nendobj\n%%EOF\n"
    )
    return crear_archivo(ruta, cuerpo)


class Taxonomia(unittest.TestCase):
    """La tabla de categorias debe ser coherente y disjunta de la audiovisual."""

    def test_casos_representativos(self) -> None:
        casos = (
            ("informe.pdf", Categoria.DOCUMENTOS, "PDF"),
            ("carta.docx", Categoria.DOCUMENTOS, "Word"),
            ("presupuesto.XLSX", Categoria.DOCUMENTOS, "Hojas-de-calculo"),
            ("charla.pptx", Categoria.DOCUMENTOS, "Presentaciones"),
            ("respaldo.sql", Categoria.DATOS, "Scripts-SQL"),
            ("app.py", Categoria.CODIGO, "Python"),
            ("analisis.ipynb", Categoria.CODIGO, "Notebooks"),
            ("libreria.jar", Categoria.COMPRIMIDOS, "ZIP"),
            ("instalador.msi", Categoria.INSTALADORES, "Ejecutables-Windows"),
            ("ubuntu.iso", Categoria.INSTALADORES, "Imagenes-de-disco"),
            ("cancion.mp3", Categoria.AUDIO, "Comprimido"),
            ("casa.skp", Categoria.DISENO, "3D"),
            ("practica.pkt", Categoria.REDES, "Packet-Tracer"),
            ("topologia.gns3", Categoria.REDES, "GNS3"),
            ("juego.nsp", Categoria.JUEGOS, "Switch"),
            ("pelicula.srt", Categoria.SUBTITULOS, "Otros"),
            ("correo.mbox", Categoria.CORREO, "Otros"),
            ("llave.pem", Categoria.LLAVES, "Otros"),
            ("maquina.ova", Categoria.VIRTUALIZACION, "Otros"),
            ("traduccion.po", Categoria.CODIGO, "Traducciones"),
        )
        for nombre, categoria, subtipo in casos:
            with self.subTest(nombre=nombre):
                self.assertEqual(clasificar_archivo(Path(nombre)), (categoria, subtipo))

    def test_extension_desconocida(self) -> None:
        self.assertIsNone(clasificar_archivo(Path("cosa.qwertyuiop")))

    def test_no_se_solapa_con_la_tabla_audiovisual(self) -> None:
        """Un `.jpg` no puede estar en las dos tablas: seria ambiguo."""
        solapadas = extensiones_registradas() & EXTENSIONES_CONOCIDAS
        self.assertEqual(solapadas, frozenset(), f"extensiones en ambas tablas: {solapadas}")

    def test_toda_categoria_tiene_carpeta(self) -> None:
        for categoria in Categoria:
            with self.subTest(categoria=categoria):
                self.assertTrue(categoria.carpeta)

    def test_ningun_nombre_de_salida_es_intransitable(self) -> None:
        """Una carpeta de salida no puede llamarse como algo que no se recorre.

        Regresion: el subtipo de instaladores se llamaba `Windows`, que esta en
        `CARPETAS_NO_TRANSITABLES`. La carpeta se creaba, pero el escaneo nunca
        volvia a entrar en ella y la validacion de rutas rechazaba apuntar la
        herramienta ahi, dejando esos archivos en un punto ciego.
        """
        from mediaorg.categorias import _TABLA
        from mediaorg.config import CARPETAS_NO_TRANSITABLES
        from mediaorg.tipos import TipoMedia

        nombres = {c.carpeta for c in Categoria}
        nombres |= {t.carpeta for t in TipoMedia}
        nombres |= {subtipo for _, subtipo in _TABLA.values()}
        colisiones = {n for n in nombres if n.casefold() in CARPETAS_NO_TRANSITABLES}
        self.assertEqual(
            colisiones, set(), f"nombres de salida que el escaneo ignoraria: {colisiones}"
        )


class FechaDeDocumentos(unittest.TestCase):
    """Metadatos internos de documentos, el equivalente al EXIF."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def test_docx(self) -> None:
        ruta = _docx(self.base / "carta.docx", "2021-03-04T05:06:07Z")
        self.assertEqual(fecha_desde_ooxml(ruta), datetime(2021, 3, 4, 5, 6, 7))

    def test_odt(self) -> None:
        ruta = _odt(self.base / "notas.odt", "2019-11-22T08:09:10")
        self.assertEqual(fecha_desde_documento(ruta), datetime(2019, 11, 22, 8, 9, 10))

    def test_pdf(self) -> None:
        ruta = _pdf(self.base / "informe.pdf", "20240512103000")
        self.assertEqual(fecha_desde_pdf(ruta), datetime(2024, 5, 12, 10, 30, 0))

    def test_pdf_solo_con_anio_y_dia(self) -> None:
        ruta = _pdf(self.base / "corto.pdf", "20230115")
        obtenido = fecha_desde_pdf(ruta)
        self.assertIsNotNone(obtenido)
        assert obtenido is not None
        self.assertEqual(obtenido.date(), datetime(2023, 1, 15).date())

    def test_pdf_con_fecha_imposible(self) -> None:
        self.assertIsNone(fecha_desde_pdf(_pdf(self.base / "malo.pdf", "20241340999999")))

    def test_zip_corrupto_no_lanza(self) -> None:
        ruta = crear_archivo(self.base / "roto.docx", b"esto no es un zip")
        self.assertIsNone(fecha_desde_documento(ruta))

    def test_extension_sin_metadatos(self) -> None:
        ruta = crear_archivo(self.base / "datos.csv", b"a,b,c")
        self.assertIsNone(fecha_desde_documento(ruta))

    def test_el_analisis_usa_la_fecha_del_documento(self) -> None:
        ruta = _docx(self.base / "carta.docx", "2018-06-07T10:11:12")
        cfg = Configuracion(raiz=self.base, modo=ModoContenido.ARCHIVOS, procesos=1)
        items = [i for i in escanear(cfg) if i.ruta == ruta]
        self.assertEqual(len(items), 1)
        info = analizar(items[0])
        self.assertEqual(info.origen_fecha, OrigenFecha.DOCUMENTO.value)
        self.assertEqual(info.fecha, datetime(2018, 6, 7, 10, 11, 12))
        self.assertEqual(info.categoria, Categoria.DOCUMENTOS.value)
        self.assertEqual(info.subtipo, "Word")


class ProfundidadYUnidades(unittest.TestCase):
    """El recorrido acotado es lo que hace segura la organizacion de un disco."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)
        crear_archivo(self.base / "suelto.pdf", b"%PDF-1.4 suelto")
        crear_archivo(self.base / "nivel1" / "anidado.pdf", b"%PDF-1.4 anidado")
        crear_archivo(self.base / "nivel1" / "nivel2" / "profundo.pdf", b"%PDF-1.4 profundo")
        # Proyecto: no se le debe sacar nada por dentro.
        crear_archivo(self.base / "proyecto" / "requirements.txt", b"pillow\n")
        crear_archivo(self.base / "proyecto" / "informe.pdf", b"%PDF-1.4 del proyecto")
        crear_archivo(self.base / "proyecto" / "sub" / "otro.pdf", b"%PDF-1.4 mas adentro")

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def _escanear(self, **extra) -> tuple[list, ResumenEscaneo]:
        parametros = {"raiz": self.base, "modo": ModoContenido.ARCHIVOS, "procesos": 1}
        parametros.update(extra)
        cfg = Configuracion(**parametros)
        resumen = ResumenEscaneo()
        return list(escanear(cfg, resumen)), resumen

    def test_por_defecto_solo_el_primer_nivel(self) -> None:
        items, resumen = self._escanear()
        self.assertEqual([i.nombre for i in items], ["suelto.pdf"])
        self.assertEqual(resumen.carpetas_sin_recorrer, 2)

    def test_profundidad_dos_alcanza_un_nivel_mas(self) -> None:
        items, _ = self._escanear(profundidad=2)
        self.assertEqual(sorted(i.nombre for i in items), ["anidado.pdf", "suelto.pdf"])

    def test_sin_limite_no_entra_en_el_proyecto(self) -> None:
        """Aun sin limite de profundidad, la unidad se respeta."""
        items, resumen = self._escanear(profundidad=0)
        nombres = sorted(i.nombre for i in items)
        self.assertEqual(nombres, ["anidado.pdf", "profundo.pdf", "suelto.pdf"])
        self.assertNotIn("informe.pdf", nombres)
        self.assertTrue(any(u.ruta.name == "proyecto" for u in resumen.unidades))

    def test_el_marcador_se_reporta(self) -> None:
        _, resumen = self._escanear(profundidad=0)
        unidad = next(u for u in resumen.unidades if u.ruta.name == "proyecto")
        self.assertEqual(unidad.marcador, "requirements.txt")

    def test_deteccion_directa_de_unidad(self) -> None:
        self.assertEqual(marcador_de_unidad(self.base / "proyecto"), "requirements.txt")
        self.assertIsNone(marcador_de_unidad(self.base / "nivel1"))

    def test_unidad_por_sufijo_de_solucion(self) -> None:
        crear_archivo(self.base / "dotnet" / "App.sln", b"Microsoft Visual Studio Solution")
        self.assertEqual(marcador_de_unidad(self.base / "dotnet"), "App.sln")


class ModosDeContenido(unittest.TestCase):
    """`media`, `archivos` y `todo` deben filtrar exactamente lo que dicen."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)
        crear_imagen(self.base / "foto.png", (1, 2, 3))
        crear_archivo(self.base / "doc.pdf", b"%PDF-1.4 x")
        crear_archivo(self.base / "hoja.xlsx", b"PK\x03\x04 falso")

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def _nombres(self, modo: ModoContenido) -> list[str]:
        cfg = Configuracion(raiz=self.base, modo=modo, procesos=1)
        return sorted(i.nombre for i in escanear(cfg))

    def test_modo_media(self) -> None:
        self.assertEqual(self._nombres(ModoContenido.MEDIA), ["foto.png"])

    def test_modo_archivos(self) -> None:
        self.assertEqual(self._nombres(ModoContenido.ARCHIVOS), ["doc.pdf", "hoja.xlsx"])

    def test_modo_todo(self) -> None:
        self.assertEqual(
            self._nombres(ModoContenido.TODO), ["doc.pdf", "foto.png", "hoja.xlsx"]
        )

    def test_profundidad_por_defecto_de_cada_modo(self) -> None:
        self.assertEqual(Configuracion(raiz=self.base, modo=ModoContenido.MEDIA).profundidad, 0)
        self.assertEqual(Configuracion(raiz=self.base, modo=ModoContenido.ARCHIVOS).profundidad, 1)
        self.assertEqual(Configuracion(raiz=self.base, modo=ModoContenido.TODO).profundidad, 1)


class RutasProtegidas(unittest.TestCase):
    """El programa debe negarse a operar donde romperia el sistema."""

    def test_carpetas_de_sistema(self) -> None:
        for ruta in (r"C:\Windows", r"C:\Windows\System32", r"C:\Program Files"):
            with self.subTest(ruta=ruta):
                if not Path(ruta).is_dir():
                    self.skipTest(f"{ruta} no existe en esta maquina")
                with self.assertRaises(ConfiguracionInvalida):
                    Configuracion(raiz=Path(ruta))

    def test_la_carpeta_temporal_si_se_permite(self) -> None:
        """Vive bajo AppData, pero organizar ahi es inofensivo."""
        with tempfile.TemporaryDirectory() as temporal:
            cfg = Configuracion(raiz=Path(temporal))
            self.assertTrue(cfg.raiz.is_dir())

    def test_destino_en_carpeta_de_sistema_se_rechaza(self) -> None:
        """Regresion: el destino no pasaba por ninguna validacion."""
        objetivo = Path(r"C:\Windows\System32")
        if not objetivo.is_dir():
            self.skipTest("no es Windows")
        with tempfile.TemporaryDirectory() as temporal:
            with self.assertRaises(ConfiguracionInvalida):
                Configuracion(raiz=Path(temporal), destino=objetivo)


class RaizQueEsProyecto(unittest.TestCase):
    """Regresion: la carpeta de inicio estaba exenta del control de unidad.

    Apuntar la herramienta a un repositorio movia su `README.md` a
    `Documentos\\Texto\\`, sus `.py` a `Codigo\\Python\\` y hasta el propio
    `requirements.txt` que lo identificaba como proyecto.
    """

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name) / "mi-repo"
        self.base.mkdir()
        crear_archivo(self.base / "requirements.txt", b"pillow\n")
        crear_archivo(self.base / "README.md", b"# proyecto\n")
        crear_archivo(self.base / "app.py", b"print(1)\n")

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def test_se_rechaza_por_defecto(self) -> None:
        with self.assertRaises(ConfiguracionInvalida) as capturado:
            Configuracion(raiz=self.base, modo=ModoContenido.TODO)
        self.assertIn("requirements.txt", str(capturado.exception))

    def test_el_mensaje_dice_como_continuar(self) -> None:
        with self.assertRaises(ConfiguracionInvalida) as capturado:
            Configuracion(raiz=self.base)
        self.assertIn("--forzar-proyecto", str(capturado.exception))

    def test_con_forzar_proyecto_si_procede(self) -> None:
        cfg = Configuracion(raiz=self.base, modo=ModoContenido.TODO, forzar_proyecto=True)
        self.assertGreater(len(organizar(cfg).plan.movimientos), 0)

    def test_una_carpeta_contenedora_normal_si_se_acepta(self) -> None:
        contenedora = self.base.parent
        cfg = Configuracion(raiz=contenedora, modo=ModoContenido.TODO, procesos=1)
        self.assertTrue(cfg.raiz.is_dir())
        # El proyecto queda dentro y sus archivos no entran al plan.
        origenes = {m.origen.name for m in organizar(cfg).plan.movimientos}
        self.assertNotIn("requirements.txt", origenes)


class OcultosYBasuraDelSistema(unittest.TestCase):
    """Regresion: `desktop.ini` acabo en `Codigo\\Configuracion\\`."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)
        crear_archivo(self.base / "Thumbs.db", b"miniaturas")
        crear_archivo(self.base / "desktop.ini", b"[.ShellClassInfo]\n")
        crear_archivo(self.base / ".gitignore", b"*.pyc\n")
        crear_archivo(self.base / "normal.pdf", b"%PDF-1.4 real")

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def _origenes(self, **extra) -> set[str]:
        parametros = {"raiz": self.base, "modo": ModoContenido.ARCHIVOS, "procesos": 1}
        parametros.update(extra)
        return {m.origen.name for m in organizar(Configuracion(**parametros)).plan.movimientos}

    def test_no_organiza_basura_del_sistema(self) -> None:
        origenes = self._origenes()
        self.assertNotIn("Thumbs.db", origenes)
        self.assertNotIn("desktop.ini", origenes)

    def test_no_organiza_archivos_que_empiezan_por_punto(self) -> None:
        self.assertNotIn(".gitignore", self._origenes())

    def test_si_organiza_lo_normal(self) -> None:
        self.assertIn("normal.pdf", self._origenes())

    def test_con_incluir_ocultos_si_entran(self) -> None:
        origenes = self._origenes(incluir_ocultos=True)
        self.assertIn("Thumbs.db", origenes)
        self.assertIn(".gitignore", origenes)

    def test_se_cuentan_los_ignorados(self) -> None:
        from mediaorg.escaneo import ResumenEscaneo, escanear

        cfg = Configuracion(raiz=self.base, modo=ModoContenido.ARCHIVOS, procesos=1)
        resumen = ResumenEscaneo()
        list(escanear(cfg, resumen))
        self.assertEqual(resumen.ignorados_ocultos, 3)


class ModoNoOcultaCarpetasReales(unittest.TestCase):
    """Regresion: `--modo media` se saltaba una carpeta llamada `Documentos`."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)
        for relativa in ("Documentos/foto1.png", "Juegos/foto2.png", "Viajes/foto3.png"):
            crear_imagen(self.base / relativa, (7, 8, 9))

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def test_modo_media_ve_las_fotos_de_esas_carpetas(self) -> None:
        from mediaorg.escaneo import escanear

        cfg = Configuracion(raiz=self.base, modo=ModoContenido.MEDIA, procesos=1)
        vistos = {i.nombre for i in escanear(cfg)}
        self.assertEqual(vistos, {"foto1.png", "foto2.png", "foto3.png"})

    def test_modo_archivos_si_las_excluye(self) -> None:
        """En el modo que SI produce esas carpetas, excluirlas es lo correcto."""
        from mediaorg.escaneo import escanear

        cfg = Configuracion(
            raiz=self.base, modo=ModoContenido.ARCHIVOS, profundidad=0, procesos=1
        )
        rutas = cfg.rutas_excluidas()
        self.assertIn(self.base / "Documentos", rutas)
        self.assertIn(self.base / "Juegos", rutas)
        self.assertNotIn(self.base / "Viajes", rutas)
        list(escanear(cfg))


class ArbolDeArchivos(unittest.TestCase):
    """Jerarquia Categoria -> Subtipo -> Anio y corrida completa."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)
        _docx(self.base / "carta.docx", "2021-03-04T05:06:07")
        _pdf(self.base / "informe.pdf", "20240512103000")
        crear_archivo(self.base / "respaldo.sql", b"SELECT 1;")
        crear_archivo(self.base / "instalador.msi", b"MSI falso")
        crear_archivo(self.base / "sin-clasificar.qwerty", b"raro")
        crear_imagen(self.base / "foto.png", (9, 9, 9))

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def _cfg(self, **extra) -> Configuracion:
        parametros = {
            "raiz": self.base,
            "modo": ModoContenido.ARCHIVOS,
            "procesos": 1,
            "duplicados": ModoDuplicados.BYTE,
            "simulacion": True,
        }
        parametros.update(extra)
        return Configuracion(**parametros)

    def test_categoria_subtipo_anio(self) -> None:
        informe = organizar(self._cfg())
        destinos = {
            m.origen.name: m.destino.relative_to(self.base).parts
            for m in informe.plan.movimientos
        }
        self.assertEqual(destinos["carta.docx"], ("Documentos", "Word", "2021", "carta.docx"))
        self.assertEqual(destinos["informe.pdf"], ("Documentos", "PDF", "2024", "informe.pdf"))
        self.assertEqual(destinos["respaldo.sql"][:2], ("Bases-de-datos", "Scripts-SQL"))
        self.assertEqual(destinos["instalador.msi"][:2], ("Instaladores", "Ejecutables-Windows"))

    def test_sin_clasificar_va_a_otros_con_su_extension(self) -> None:
        informe = organizar(self._cfg())
        destino = next(
            m.destino.relative_to(self.base).parts
            for m in informe.plan.movimientos
            if m.origen.name == "sin-clasificar.qwerty"
        )
        self.assertEqual(destino[:2], ("Otros-Archivos", "QWERTY"))

    def test_jerarquia_solo_categoria(self) -> None:
        informe = organizar(self._cfg(jerarquia_archivos=JerarquiaArchivos.CATEGORIA))
        destino = next(
            m.destino.relative_to(self.base).parts
            for m in informe.plan.movimientos
            if m.origen.name == "carta.docx"
        )
        self.assertEqual(destino, ("Documentos", "carta.docx"))

    def test_jerarquia_categoria_anio(self) -> None:
        informe = organizar(self._cfg(jerarquia_archivos=JerarquiaArchivos.CATEGORIA_ANIO))
        destino = next(
            m.destino.relative_to(self.base).parts
            for m in informe.plan.movimientos
            if m.origen.name == "informe.pdf"
        )
        self.assertEqual(destino, ("Documentos", "2024", "informe.pdf"))

    def test_el_modo_archivos_no_toca_las_fotos(self) -> None:
        informe = organizar(self._cfg())
        self.assertNotIn("foto.png", {m.origen.name for m in informe.plan.movimientos})

    def test_modo_todo_organiza_ambos(self) -> None:
        informe = organizar(self._cfg(modo=ModoContenido.TODO))
        nombres = {m.origen.name for m in informe.plan.movimientos}
        self.assertIn("foto.png", nombres)
        self.assertIn("carta.docx", nombres)
        destino_foto = next(
            m.destino.relative_to(self.base).parts
            for m in informe.plan.movimientos
            if m.origen.name == "foto.png"
        )
        self.assertEqual(destino_foto[0], "Fotos")

    def test_ejecucion_real_y_reversion(self) -> None:
        from mediaorg.ejecutor import deshacer

        antes = sorted(p.name for p in self.base.iterdir() if p.is_file())
        informe = organizar(self._cfg(simulacion=False, modo=ModoContenido.TODO))
        self.assertEqual(informe.ejecucion.fallidos, 0)
        self.assertTrue((self.base / "Documentos" / "PDF").is_dir())
        assert informe.manifiesto is not None
        deshacer(informe.manifiesto, simulacion=False)
        despues = sorted(p.name for p in self.base.iterdir() if p.is_file())
        self.assertEqual(antes, despues)

    def test_mtime_absurdo_no_crea_una_carpeta_de_1980(self) -> None:
        """Una fecha invalida cae al siguiente candidato, no se usa tal cual."""
        import os

        raro = crear_archivo(self.base / "otro" / "x.sql", b"SELECT 2;")
        antiguo = datetime(1980, 1, 1).timestamp()
        os.utime(raro, (antiguo, antiguo))
        informe = organizar(self._cfg(raiz=self.base / "otro"))
        destino = informe.plan.movimientos[0].destino.relative_to(self.base / "otro").parts
        self.assertNotEqual(destino[2], "1980")
        self.assertGreaterEqual(int(destino[2]), 1990)

    def test_sin_fecha_alguna_va_a_la_carpeta_sin_fecha(self) -> None:
        """Cuando la cascada completa falla, el archivo no recibe fecha inventada."""
        from mediaorg.escaneo import ArchivoEncontrado
        from mediaorg.metadatos import Analisis
        from mediaorg.planificador import construir_plan
        from mediaorg.tipos import TipoMedia

        item = ArchivoEncontrado(
            ruta=self.base / "respaldo.sql", tamano=9, mtime=0.0, tipo=TipoMedia.DESCONOCIDO
        )
        info = Analisis(
            ruta=item.ruta,
            fecha_iso=None,
            origen_fecha="desconocido",
            fuente="Otros",
            firma=None,
            tipo_efectivo=TipoMedia.DESCONOCIDO.value,
            categoria=Categoria.DATOS.value,
            subtipo="Scripts-SQL",
        )
        plan = construir_plan([item], {item.ruta: info}, [], self._cfg())
        destino = plan.movimientos[0].destino.relative_to(self.base).parts
        self.assertEqual(destino, ("Bases-de-datos", "Scripts-SQL", CARPETA_SIN_FECHA, "respaldo.sql"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
