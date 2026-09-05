"""Cliente de Anthropic: streaming, tiempo limite y contabilidad de tokens.

Todo fallo del proveedor se traduce a `503 AI_UNAVAILABLE` y nada se persiste.
Esa es la propiedad que hace que el reporte sea aditivo y no la fuente de verdad:
si Claude cae, las metricas de la simulacion siguen visibles y guardables
(docs/AI_PROMPTS.md §4).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from app.config import Settings
from app.errors import ApiError
from app.prompts.report_es import SYSTEM_PROMPT

logger = logging.getLogger(__name__)


@dataclass
class StreamAccounting:
    """Se rellena mientras el texto fluye; se lee al terminar."""

    input_tokens: int = 0
    output_tokens: int = 0
    chunks: list[str] = field(default_factory=list)

    # El modelo alcanzo el tope de tokens y la respuesta quedo cortada a mitad
    # de frase. Hay que saberlo: un reporte truncado se ve roto, y el fallo es
    # silencioso —llega un 200 con texto que simplemente termina de golpe—.
    truncated: bool = False

    @property
    def text(self) -> str:
        return "".join(self.chunks)


class ClaudeService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self):  # noqa: ANN202 - el tipo depende del SDK instalado
        if not self._settings.anthropic_api_key:
            raise ApiError(
                "AI_UNAVAILABLE",
                "La generacion de reportes no esta disponible: falta ANTHROPIC_API_KEY.",
            )
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise ApiError(
                "AI_UNAVAILABLE",
                "El SDK de Anthropic no esta instalado.",
            ) from exc

        return AsyncAnthropic(
            api_key=self._settings.anthropic_api_key,
            timeout=self._settings.ai_timeout_seconds,
            max_retries=1,
        )

    async def stream_report(
        self,
        user_prompt: str,
        accounting: StreamAccounting,
    ) -> AsyncIterator[str]:
        """Emite fragmentos de texto conforme llegan.

        El streaming no es cosmetico: convierte los ~15 s de un reporte de 1200
        tokens en texto que aparece de inmediato, y es lo que le da a la voz
        frases que leer en lugar de un silencio hasta el bloque completo.
        """
        client = self._client()

        try:
            from anthropic import (
                APIConnectionError,
                APIStatusError,
                APITimeoutError,
                RateLimitError,
            )
        except ImportError as exc:  # pragma: no cover
            raise ApiError("AI_UNAVAILABLE", "El SDK de Anthropic no esta instalado.") from exc

        try:
            async with client.messages.stream(
                model=self._settings.anthropic_model,
                max_tokens=self._settings.ai_max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            ) as stream:
                async for fragment in stream.text_stream:
                    if fragment:
                        accounting.chunks.append(fragment)
                        yield fragment

                final = await stream.get_final_message()
                accounting.input_tokens = getattr(final.usage, "input_tokens", 0) or 0
                accounting.output_tokens = getattr(final.usage, "output_tokens", 0) or 0
                accounting.truncated = getattr(final, "stop_reason", None) == "max_tokens"

        except RateLimitError as exc:
            logger.warning("Anthropic devolvio 429: %s", exc)
            raise ApiError(
                "AI_UNAVAILABLE",
                "El servicio de IA esta saturado. Reintenta en unos minutos.",
            ) from exc
        except APITimeoutError as exc:
            logger.warning("Anthropic excedio el tiempo limite")
            raise ApiError(
                "AI_UNAVAILABLE",
                "El servicio de IA no respondio a tiempo. Puedes reintentar.",
            ) from exc
        except (APIConnectionError, APIStatusError) as exc:
            logger.warning("Fallo de Anthropic: %s", type(exc).__name__)
            raise ApiError(
                "AI_UNAVAILABLE",
                "El servicio de IA no esta disponible. Las metricas siguen guardadas.",
            ) from exc

        if not accounting.text.strip():
            # Una respuesta vacia es un fallo, no un reporte corto: no se
            # persiste (AI_PROMPTS §4).
            raise ApiError("AI_UNAVAILABLE", "El servicio de IA devolvio una respuesta vacia.")
