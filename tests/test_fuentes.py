"""Pruebas del clasificador de fuente.

Cada caso proviene de un nombre real observado en el acervo de 57.701
archivos que motivo estas reglas.
"""

from __future__ import annotations

import unittest

from mediaorg.fuentes import Fuente, clasificar_fuente


class Clasificacion(unittest.TestCase):
    """Tabla de casos nombre -> fuente esperada."""

    CASOS = (
        ("IMG-20190606-WA0044.jpg", Fuente.WHATSAPP),
        ("VID-20221020-WA0039.mp4", Fuente.WHATSAPP),
        ("IMG-20211231-WA0011.jpeg", Fuente.WHATSAPP),
        ("Screenshot_20240608_193933_WhatsApp.jpg", Fuente.CAPTURAS),
        ("Screenshot_20220513-191524_Chrome.jpg", Fuente.CAPTURAS),
        ("Screen_Recording_20240808_203118_Parental Control.mp4", Fuente.CAPTURAS),
        ("FB_IMG_1561306096025.jpg", Fuente.REDES),
        ("received_1561306096025.jpg", Fuente.REDES),
        ("20230726_124814.jpg", Fuente.CAMARA),
        ("20241109_082941.mp4", Fuente.CAMARA),
        ("VID_20241209_120302_638.mp4", Fuente.CAMARA),
        ("IMG_20240318_161153_892.webp", Fuente.CAMARA),
        ("IMG_0042.jpg", Fuente.CAMARA),
        ("20231008_171110-01.jpeg", Fuente.EDITADAS),
        ("penup_20240605_174929.jpg", Fuente.EDITADAS),
        ("collage_2024.jpg", Fuente.EDITADAS),
        ("1000035167.jpg", Fuente.GALERIA),
        ("1000310863.jpg", Fuente.GALERIA),
        ("7b0393d7-43ed-44ed-ae2a-17281db6a777.jpg", Fuente.DESCARGAS),
        ("7dbf81f866b2d75f85d59aedf0a2709825518a5e-ReadEra.jpg", Fuente.DESCARGAS),
        ("download_1.jpg", Fuente.DESCARGAS),
        ("vacaciones familia.jpg", Fuente.OTROS),
    )

    def test_tabla_completa(self) -> None:
        for nombre, esperada in self.CASOS:
            with self.subTest(nombre=nombre):
                self.assertEqual(clasificar_fuente(nombre), esperada)

    def test_captura_de_whatsapp_no_es_whatsapp(self) -> None:
        """Una captura de la app es una captura, no un archivo recibido."""
        self.assertEqual(
            clasificar_fuente("Screenshot_20240608_193933_WhatsApp.jpg"), Fuente.CAPTURAS
        )

    def test_editada_de_camara_prioriza_edicion(self) -> None:
        """El sufijo `-01` lo agrega el editor sobre una foto de camara."""
        self.assertEqual(clasificar_fuente("20231008_171110-01.jpeg"), Fuente.EDITADAS)

    def test_es_insensible_a_mayusculas(self) -> None:
        self.assertEqual(clasificar_fuente("fb_img_1561306096025.JPG"), Fuente.REDES)

    def test_nombre_vacio_no_falla(self) -> None:
        self.assertEqual(clasificar_fuente(""), Fuente.OTROS)

    def test_toda_fuente_produce_carpeta_no_vacia(self) -> None:
        for fuente in Fuente:
            with self.subTest(fuente=fuente):
                self.assertTrue(fuente.carpeta)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
