"""Jerarquia de excepciones del organizador.

Toda falla previsible se modela con una excepcion propia para que el CLI
pueda traducirla a un codigo de salida sin capturar `Exception` a ciegas.
"""

from __future__ import annotations

__all__ = [
    "ErrorOrganizador",
    "ConfiguracionInvalida",
    "RutaInsegura",
    "ErrorDeEjecucion",
    "ErrorDeManifiesto",
]


class ErrorOrganizador(Exception):
    """Raiz de todos los errores controlados del paquete."""


class ConfiguracionInvalida(ErrorOrganizador):
    """Los parametros recibidos no forman una configuracion utilizable."""


class RutaInsegura(ErrorOrganizador):
    """Una ruta calculada se sale del arbol permitido o no es representable."""


class ErrorDeEjecucion(ErrorOrganizador):
    """Fallo al materializar un movimiento en el sistema de archivos."""


class ErrorDeManifiesto(ErrorOrganizador):
    """El manifiesto de reversion esta ausente, corrupto o es incompatible."""
