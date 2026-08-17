"""Pruebas de la cascada de resolucion de fechas."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from mediaorg.fechas import (
    OrigenFecha,
    etiqueta_mes,
    fecha_desde_contenedor,
    fecha_desde_exif,
    fecha_desde_nombre,
    resolver_fecha,
)
from mediaorg.tipos import TipoMedia

from .ayudas import crear_imagen, crear_jpeg_con_exif, crear_mp4


class FechaDesdeNombre(unittest.TestCase):
    """Los patrones estan calibrados contra nombres reales del acervo."""

    def test_patron_whatsapp(self) -> None:
        resultado = fecha_desde_nombre("IMG-20190606-WA0044.jpg")
        self.assertIsNotNone(resultado)
        assert resultado is not None
        self.assertEqual(resultado.date(), datetime(2019, 6, 6).date())

    def test_patron_camara_samsung(self) -> None:
        self.assertEqual(
            fecha_desde_nombre("20230726_124814.jpg"),
            datetime(2023, 7, 26, 12, 48, 14),
        )

    def test_patron_captura_con_guion(self) -> None:
        self.assertEqual(
            fecha_desde_nombre("Screenshot_20220513-191524_Chrome.jpg"),
            datetime(2022, 5, 13, 19, 15, 24),
        )

    def test_patron_captura_con_app(self) -> None:
        self.assertEqual(
            fecha_desde_nombre("Screenshot_20240608_193933_WhatsApp.jpg"),
            datetime(2024, 6, 8, 19, 39, 33),
        )

    def test_patron_video_camara(self) -> None:
        self.assertEqual(
            fecha_desde_nombre("VID_20241209_120302_638.mp4"),
            datetime(2024, 12, 9, 12, 3, 2),
        )

    def test_epoca_en_milisegundos_con_prefijo(self) -> None:
        resultado = fecha_desde_nombre("FB_IMG_1704399976041.jpg")
        self.assertIsNotNone(resultado)
        assert resultado is not None
        self.assertEqual(resultado.year, 2024)

    def test_id_numerico_de_galeria_no_es_fecha(self) -> None:
        """`1000035167` son 10 digitos: leerlo como epoca daria 2001."""
        self.assertIsNone(fecha_desde_nombre("1000035167.jpg"))

    def test_blob_hexadecimal_no_es_fecha(self) -> None:
        self.assertIsNone(
            fecha_desde_nombre("7dbf81f866b2d75f85d59aedf0a2709825518a5e-ReadEra.jpg")
        )

    def test_uuid_no_es_fecha(self) -> None:
        self.assertIsNone(fecha_desde_nombre("7b0393d7-43ed-44ed-ae2a-17281db6a777.jpg"))

    def test_iso_con_hora_estilo_whatsapp(self) -> None:
        self.assertEqual(
            fecha_desde_nombre("WhatsApp Image 2024-05-12 at 14.30.22.jpeg"),
            datetime(2024, 5, 12, 14, 30, 22),
        )

    def test_fecha_invalida_se_rechaza(self) -> None:
        self.assertIsNone(fecha_desde_nombre("20241340_999999.jpg"))

    def test_anio_previo_al_limite_se_rechaza(self) -> None:
        self.assertIsNone(fecha_desde_nombre("19700101_101010.jpg"))

    def test_fecha_futura_se_rechaza(self) -> None:
        futuro = datetime.now().year + 3
        self.assertIsNone(fecha_desde_nombre(f"{futuro}0101_101010.jpg"))


class FechaDesdeMetadatos(unittest.TestCase):
    """EXIF y contenedores reales, escritos en archivos temporales."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def test_exif_datetime_original(self) -> None:
        esperado = datetime(2021, 3, 4, 5, 6, 7)
        ruta = crear_jpeg_con_exif(self.base / "foto.jpg", esperado)
        self.assertEqual(fecha_desde_exif(ruta), esperado)

    def test_imagen_sin_exif_devuelve_none(self) -> None:
        ruta = crear_imagen(self.base / "plano.png")
        self.assertIsNone(fecha_desde_exif(ruta))

    def test_archivo_inexistente_no_lanza(self) -> None:
        self.assertIsNone(fecha_desde_exif(self.base / "no-existe.jpg"))

    def test_mvhd_con_moov_al_final(self) -> None:
        esperado = datetime(2024, 5, 12, 10, 0, 0)
        ruta = crear_mp4(self.base / "video.mp4", esperado)
        obtenido = fecha_desde_contenedor(ruta)
        self.assertIsNotNone(obtenido)
        assert obtenido is not None
        # El contenedor guarda UTC; la conversion a local puede desplazar horas.
        self.assertLess(abs((obtenido - esperado).total_seconds()), 24 * 3600)

    def test_mvhd_en_cero_se_ignora(self) -> None:
        """WhatsApp deja `creation_time = 0`: no es una fecha valida."""
        ruta = crear_mp4(self.base / "wa.mp4", None)
        self.assertIsNone(fecha_desde_contenedor(ruta))


class Cascada(unittest.TestCase):
    """Prioridad entre las cuatro fuentes de fecha."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def test_exif_gana_al_nombre(self) -> None:
        esperado = datetime(2015, 1, 2, 3, 4, 5)
        ruta = crear_jpeg_con_exif(self.base / "20230726_124814.jpg", esperado)
        resultado = resolver_fecha(ruta, TipoMedia.FOTO)
        self.assertEqual(resultado.origen, OrigenFecha.EXIF)
        self.assertEqual(resultado.valor, esperado)

    def test_nombre_gana_al_sistema(self) -> None:
        ruta = crear_imagen(self.base / "20230726_124814.png")
        resultado = resolver_fecha(ruta, TipoMedia.FOTO)
        self.assertEqual(resultado.origen, OrigenFecha.NOMBRE)
        self.assertTrue(resultado.confiable)

    def test_respaldo_al_sistema(self) -> None:
        ruta = crear_imagen(self.base / "1000035167.png")
        resultado = resolver_fecha(ruta, TipoMedia.FOTO)
        self.assertEqual(resultado.origen, OrigenFecha.SISTEMA)
        self.assertFalse(resultado.confiable)
        self.assertIsNotNone(resultado.valor)

    def test_video_usa_contenedor_antes_del_nombre(self) -> None:
        ruta = crear_mp4(self.base / "20200101_010101.mp4", datetime(2024, 5, 12, 10, 0, 0))
        resultado = resolver_fecha(ruta, TipoMedia.VIDEO)
        self.assertEqual(resultado.origen, OrigenFecha.CONTENEDOR)

    def test_video_de_mensajeria_cae_al_nombre(self) -> None:
        ruta = crear_mp4(self.base / "VID-20221020-WA0039.mp4", None)
        resultado = resolver_fecha(ruta, TipoMedia.VIDEO)
        self.assertEqual(resultado.origen, OrigenFecha.NOMBRE)
        assert resultado.valor is not None
        self.assertEqual(resultado.valor.date(), datetime(2022, 10, 20).date())


class Etiquetas(unittest.TestCase):
    """Formato de los nombres de carpeta de fecha."""

    def test_mes_numerico(self) -> None:
        self.assertEqual(etiqueta_mes(datetime(2024, 5, 1)), "2024-05")

    def test_mes_con_nombre(self) -> None:
        self.assertEqual(etiqueta_mes(datetime(2024, 5, 1), True), "2024-05-Mayo")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
