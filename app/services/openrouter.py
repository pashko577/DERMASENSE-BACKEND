"""Cliente de OpenRouter, alternativo al de Anthropic.

Por que existe: OpenRouter permite servir **el mismo `claude-sonnet-5`** que fija
[TRD §1](../../DERMASENSE/docs/TRD.md), a la misma tarifa ($2 / $10 por millon de
tokens), con una sola clave. Cambia el transporte, no el modelo, asi que la
documentacion del proyecto sigue siendo cierta.

Lo que si cambia es la forma de la API: OpenRouter habla el formato de OpenAI
(`/chat/completions`), no la Messages API de Anthropic. Este modulo absorbe esa
diferencia y expone exactamente el mismo contrato que `ClaudeService`, de modo
que `reports.py` no sabe —ni necesita saber— por donde sale la peticion.

El modo de fallo se conserva intacto: cualquier problema del proveedor se
traduce a `AI_UNAVAILABLE` y nada se persiste (docs/AI_PROMPTS.md §4).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from app.config import Settings
from app.errors import ApiError
from app.prompts.report_es import SYSTEM_PROMPT
from app.services.claude import StreamAccounting

logger = logging.getLogger(__name__)


class OpenRouterService:
    """Mismo contrato que `ClaudeService`, distinto transporte."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self):  # noqa: ANN202 - el tipo depende del SDK instalado
        if not self._settings.openrouter_api_key:
            raise ApiError(
                "AI_UNAVAILABLE",
                "La generacion de reportes no esta disponible: falta OPENROUTER_API_KEY.",
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise ApiError(
                "AI_UNAVAILABLE",
                "El SDK de OpenAI (usado como cliente de OpenRouter) no esta instalado.",
            ) from exc

        return AsyncOpenAI(
            api_key=self._settings.openrouter_api_key,
            base_url=self._settings.openrouter_base_url,
            timeout=self._settings.ai_timeout_seconds,
            max_retries=1,
            # OpenRouter usa estas cabeceras para atribuir el trafico. Son
            # opcionales y no afectan al enrutado.
            default_headers={
                "HTTP-Referer": self._settings.openrouter_site_url,
                "X-Title": self._settings.openrouter_app_name,
            },
        )

    async def stream_report(
        self,
        user_prompt: str,
        accounting: StreamAccounting,
    ) -> AsyncIterator[str]:
        client = self._client()

        try:
            from openai import (
                APIConnectionError,
                APIStatusError,
                APITimeoutError,
                AuthenticationError,
                RateLimitError,
            )
        except ImportError as exc:  # pragma: no cover
            raise ApiError("AI_UNAVAILABLE", "El SDK de OpenAI no esta instalado.") from exc

        try:
            stream = await client.chat.completions.create(
                model=self._settings.ai_model,
                max_tokens=self._settings.ai_max_tokens,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                stream=True,
                # Sin esto el ultimo fragmento no trae el recuento y la
                # contabilidad de tokens quedaria en cero.
                stream_options={"include_usage": True},
            )

            async for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    accounting.input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                    accounting.output_tokens = getattr(usage, "completion_tokens", 0) or 0

                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                if getattr(choice, "finish_reason", None) == "length":
                    accounting.truncated = True

                fragment = choice.delta.content
                if fragment:
                    accounting.chunks.append(fragment)
                    yield fragment

        except AuthenticationError as exc:
            logger.error("OpenRouter rechazo la clave de API")
            raise ApiError(
                "AI_UNAVAILABLE",
                "El proveedor de IA rechazo la credencial. Revisa OPENROUTER_API_KEY.",
            ) from exc
        except RateLimitError as exc:
            logger.warning("OpenRouter devolvio 429")
            raise ApiError(
                "AI_UNAVAILABLE",
                "El servicio de IA esta saturado. Reintenta en unos minutos.",
            ) from exc
        except APITimeoutError as exc:
            logger.warning("OpenRouter excedio el tiempo limite")
            raise ApiError(
                "AI_UNAVAILABLE",
                "El servicio de IA no respondio a tiempo. Puedes reintentar.",
            ) from exc
        except (APIConnectionError, APIStatusError) as exc:
            logger.warning("Fallo de OpenRouter: %s", type(exc).__name__)
            raise ApiError(
                "AI_UNAVAILABLE",
                "El servicio de IA no esta disponible. Las metricas siguen guardadas.",
            ) from exc

        if not accounting.text.strip():
            raise ApiError("AI_UNAVAILABLE", "El servicio de IA devolvio una respuesta vacia.")
