"""Pruebas del saneado de rutas y de la resolucion de colisiones."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath

from mediaorg.errores import RutaInsegura
from mediaorg.rutas_seguras import (
    LARGO_MAXIMO_COMPONENTE,
    asegurar_directorio,
    es_subruta,
    nombre_disponible,
    ruta_del_sistema,
    sanear_componente,
)


class Saneado(unittest.TestCase):
    """Ningun dato del usuario debe poder generar un componente invalido."""

    def test_quita_separadores(self) -> None:
        self.assertNotIn("/", sanear_componente("a/b"))
        self.assertNotIn("\\", sanear_componente("a\\b"))

    def test_quita_caracteres_prohibidos(self) -> None:
        resultado = sanear_componente('a<b>c:d"e|f?g*h')
        for prohibido in '<>:"|?*':
            self.assertNotIn(prohibido, resultado)

    def test_quita_control(self) -> None:
        self.assertNotIn("\x00", sanear_componente("a\x00b"))
        self.assertNotIn("\n", sanear_componente("a\nb"))

    def test_intento_de_recorrido_de_directorio(self) -> None:
        resultado = sanear_componente("../../Windows/System32")
        self.assertNotIn("/", resultado)
        self.assertNotIn("\\", resultado)

    def test_nombre_reservado_de_windows(self) -> None:
        self.assertEqual(sanear_componente("CON"), "CON_")
        self.assertEqual(sanear_componente("com1.txt"), "com1.txt_")

    def test_no_deja_puntos_ni_espacios_finales(self) -> None:
        self.assertFalse(sanear_componente("carpeta.  ").endswith((".", " ")))

    def test_cadena_vacia_usa_respaldo(self) -> None:
        self.assertEqual(sanear_componente("   "), "Sin-Nombre")
        self.assertEqual(sanear_componente("..."), "Sin-Nombre")

    def test_acota_el_largo(self) -> None:
        self.assertLessEqual(len(sanear_componente("z" * 500)), LARGO_MAXIMO_COMPONENTE)

    def test_conserva_texto_valido(self) -> None:
        self.assertEqual(sanear_componente("2024-05-Mayo"), "2024-05-Mayo")
        self.assertEqual(sanear_componente("Redes-Sociales"), "Redes-Sociales")


class Contencion(unittest.TestCase):
    """`es_subruta` es la barrera contra escrituras fuera del arbol."""

    def test_hijo_directo(self) -> None:
        self.assertTrue(es_subruta(PureWindowsPath(r"C:\a\b"), PureWindowsPath(r"C:\a")))

    def test_igual_cuenta_como_contenido(self) -> None:
        self.assertTrue(es_subruta(PureWindowsPath(r"C:\a"), PureWindowsPath(r"C:\a")))

    def test_hermano_no_esta_contenido(self) -> None:
        self.assertFalse(es_subruta(PureWindowsPath(r"C:\otro"), PureWindowsPath(r"C:\a")))

    def test_prefijo_de_texto_no_basta(self) -> None:
        self.assertFalse(es_subruta(PureWindowsPath(r"C:\abc"), PureWindowsPath(r"C:\ab")))

    def test_posix(self) -> None:
        self.assertTrue(es_subruta(PurePosixPath("/a/b/c"), PurePosixPath("/a")))
        self.assertFalse(es_subruta(PurePosixPath("/x"), PurePosixPath("/a")))


class RutaLarga(unittest.TestCase):
    """El prefijo de ruta larga es obligatorio en Windows."""

    @unittest.skipUnless(os.name == "nt", "solo aplica en Windows")
    def test_agrega_prefijo(self) -> None:
        self.assertTrue(ruta_del_sistema(Path(r"C:\a\b")).startswith("\\\\?\\"))

    @unittest.skipUnless(os.name == "nt", "solo aplica en Windows")
    def test_no_duplica_prefijo(self) -> None:
        una = ruta_del_sistema(Path(r"C:\a"))
        self.assertEqual(ruta_del_sistema(Path(una)), una)

    @unittest.skipIf(os.name == "nt", "comportamiento POSIX")
    def test_posix_sin_prefijo(self) -> None:
        self.assertEqual(ruta_del_sistema(Path("/a/b")), "/a/b")


class Colisiones(unittest.TestCase):
    """Dos archivos distintos nunca deben aterrizar en la misma ruta."""

    def setUp(self) -> None:
        self._temporal = tempfile.TemporaryDirectory()
        self.base = Path(self._temporal.name)

    def tearDown(self) -> None:
        self._temporal.cleanup()

    def test_ruta_libre_se_devuelve_igual(self) -> None:
        destino = self.base / "foto.jpg"
        self.assertEqual(nombre_disponible(destino, set()), destino)

    def test_archivo_existente_genera_sufijo(self) -> None:
        destino = self.base / "foto.jpg"
        destino.write_bytes(b"x")
        self.assertEqual(nombre_disponible(destino, set()).name, "foto~1.jpg")

    def test_reserva_en_memoria_tambien_cuenta(self) -> None:
        destino = self.base / "foto.jpg"
        self.assertEqual(nombre_disponible(destino, {destino}).name, "foto~1.jpg")

    def test_sufijos_consecutivos(self) -> None:
        destino = self.base / "foto.jpg"
        destino.write_bytes(b"x")
        reservados = {self.base / "foto~1.jpg"}
        self.assertEqual(nombre_disponible(destino, reservados).name, "foto~2.jpg")

    def test_archivo_sin_extension(self) -> None:
        destino = self.base / "sinext"
        destino.write_bytes(b"x")
        self.assertEqual(nombre_disponible(destino, set()).name, "sinext~1")

    def test_crear_directorio_es_idempotente(self) -> None:
        objetivo = self.base / "a" / "b" / "c"
        asegurar_directorio(objetivo)
        asegurar_directorio(objetivo)
        self.assertTrue(objetivo.is_dir())

    def test_directorio_sobre_archivo_falla_controlado(self) -> None:
        archivo = self.base / "archivo"
        archivo.write_bytes(b"x")
        with self.assertRaises(RutaInsegura):
            asegurar_directorio(archivo / "hijo")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
