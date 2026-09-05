"""Seleccion del proveedor de IA.

El endpoint de reportes no conoce proveedores: recibe algo que cumple
`ReportGenerator` y lo consume. Esa indireccion cuesta veinte lineas y compra dos
cosas concretas —cambiar de proveedor sin tocar `reports.py`, y sustituirlo por
un doble en las pruebas sin parchear nada—.

El modelo no cambia entre proveedores: ambos sirven `claude-sonnet-5`, que es lo
que fija TRD §1. Lo que cambia es por donde sale la peticion.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from app.config import Settings
from app.services.claude import ClaudeService, StreamAccounting


class ReportGenerator(Protocol):
    """El contrato que `reports.py` consume."""

    def stream_report(
        self, user_prompt: str, accounting: StreamAccounting
    ) -> AsyncIterator[str]: ...


def build_report_generator(settings: Settings) -> ReportGenerator:
    if settings.ai_provider == "openrouter":
        # Importacion diferida: quien use Anthropic no necesita el SDK de OpenAI
        # instalado, y viceversa.
        from app.services.openrouter import OpenRouterService

        return OpenRouterService(settings)
    return ClaudeService(settings)
