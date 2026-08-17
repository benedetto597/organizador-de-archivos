"""Pruebas de la deteccion de tipo por extension y por firma binaria."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mediaorg.tipos import TipoMedia, clasificar, tipo_por_extension, tipo_por_firma

from .ayudas import crear_archivo, crear_imagen, crear_mp4


class PorExtension(unittest.TestCase):
    """Clasificacion sin abrir el archivo."""

    def test_extensiones_de_foto(self) -> None:
        for extension in (".jpg", ".JPEG", ".png", ".webp", ".heic"):
            with self.subTest(extension=extension):
                self.assertEqual(
                    tipo_por_extension(Path(f"x{extension}")), TipoMedia.FOTO
                )

    def test_extensiones_de_video(self) -> None:
        for extension in (".mp4", ".MOV", ".3gp", ".mkv"):
            with self.subTest(extension=extension):
                self.assertEqual(
                    tipo_por_extension(Path(f"x{extension}")), TipoMedia.VIDEO
                )

    def test_gif_es_animacion(self) -> None:
        self.assertEqual(tipo_por_extension(Path("x.gif")), TipoMedia.ANIMACION)

    def test_extension_ajena(self) -> None:
        self.assertEqual(tipo_por_extension(Path("notas.txt")), TipoMedia.DESCONOCIDO)


class PorFirma(unittest.TestCase):
    """Clasificacion por bytes magicos."""

    def test_jpeg(self) -> None:
        self.assertEqual(tipo_por_firma(b"\xff\xd8\xff\xe0" + b"\x00" * 8), TipoMedia.FOTO)

    def test_png(self) -> None:
        self.assertEqual(tipo_por_firma(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8), TipoMedia.FOTO)

    def test_isobmff_video(self) -> None:
        self.assertEqual(tipo_por_firma(b"\x00\x00\x00\x18ftypisom"), TipoMedia.VIDEO)

    def test_isobmff_heic_es_foto(self) -> None:
        self.assertEqual(tipo_por_firma(b"\x00\x00\x00\x18ftypheic"), TipoMedia.FOTO)

    def test_riff_webp(self) -> None:
        self.assertEqual(tipo_por_firma(b"RIFF\x00\x00\x00\x00WEBP"), TipoMedia.FOTO)

    def test_riff_avi(self) -> None:
        self.assertEqual(tipo_por_firma(b"RIFF\x00\x00\x00\x00AVI "), TipoMedia.VIDEO)

    def test_matroska(self) -> None:
        self.assertEqual(tipo_por_firma(b"\x1a\x45\xdf\xa3" + b"\x00" * 8), TipoMedia.VIDEO)

    def test_cabecera_corta(self) -> None:
        self.assertIsNone(tipo_por_firma(b"ab"))

    def test_firma_desconocida(self) -> None:
        self.assertIsNone(tipo_por_firma(b"ESTONOESNADA" + b"\x00" * 8))


class Combinada(unittest.TestCase):
    """La firma manda cuando contradice a la extension."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def test_video_disfrazado_de_jpg(self) -> None:
        ruta = crear_mp4(self.base / "falso.jpg", None)
        self.assertEqual(clasificar(ruta), TipoMedia.VIDEO)

    def test_png_real(self) -> None:
        ruta = crear_imagen(self.base / "real.png")
        self.assertEqual(clasificar(ruta), TipoMedia.FOTO)

    def test_extension_conocida_con_contenido_irreconocible(self) -> None:
        ruta = crear_archivo(self.base / "raro.jpg", b"contenido cualquiera 1234")
        self.assertEqual(clasificar(ruta), TipoMedia.FOTO)

    def test_archivo_ajeno(self) -> None:
        ruta = crear_archivo(self.base / "notas.txt", b"hola")
        self.assertEqual(clasificar(ruta), TipoMedia.DESCONOCIDO)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
