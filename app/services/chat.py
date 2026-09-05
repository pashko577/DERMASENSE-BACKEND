"""Completado conversacional, con el proveedor de IA que este activo.

El reporte se sirve en streaming porque tarda ~15 s y hay que darle frases a la
voz. El chat no: son respuestas de dos a cinco frases, y esperar el bloque
completo cuesta menos que la complejidad de partirlo en trozos.

Cubre los tres proveedores por el mismo camino que `ai.py`: Anthropic por su
Messages API, y OpenRouter/Groq por el formato de OpenAI.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import Settings
from app.errors import ApiError

logger = logging.getLogger(__name__)

# Una respuesta conversacional no necesita el margen del reporte tecnico.
MAX_TOKENS = 700


async def complete(
    settings: Settings,
    system: str,
    messages: list[dict[str, str]],
) -> tuple[str, int, int]:
    """Devuelve (texto, tokens_entrada, tokens_salida)."""
    if not settings.ai_enabled:
        raise ApiError(
            "AI_UNAVAILABLE",
            f"El asistente no esta configurado: falta {settings.ai_key_variable}.",
        )

    if settings.ai_provider == "anthropic":
        return await _complete_anthropic(settings, system, messages)
    return await _complete_openai_compatible(settings, system, messages)


async def _complete_anthropic(
    settings: Settings, system: str, messages: list[dict[str, str]]
) -> tuple[str, int, int]:
    try:
        from anthropic import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AsyncAnthropic,
            RateLimitError,
        )
    except ImportError as exc:  # pragma: no cover
        raise ApiError("AI_UNAVAILABLE", "El SDK de Anthropic no esta instalado.") from exc

    client = AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        timeout=settings.ai_timeout_seconds,
        max_retries=1,
    )

    try:
        response = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,  # type: ignore[arg-type]
        )
    except RateLimitError as exc:
        raise ApiError(
            "AI_UNAVAILABLE", "El asistente esta saturado. Reintenta en un momento."
        ) from exc
    except (APITimeoutError, APIConnectionError, APIStatusError) as exc:
        logger.warning("Fallo de Anthropic en chat: %s", type(exc).__name__)
        raise ApiError("AI_UNAVAILABLE", "El asistente no esta disponible ahora mismo.") from exc

    block: Any = response.content[0] if response.content else None
    text = getattr(block, "text", "") if block else ""
    usage = response.usage
    return text, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0


async def _complete_openai_compatible(
    settings: Settings, system: str, messages: list[dict[str, str]]
) -> tuple[str, int, int]:
    try:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AsyncOpenAI,
            AuthenticationError,
            RateLimitError,
        )
    except ImportError as exc:  # pragma: no cover
        raise ApiError("AI_UNAVAILABLE", "El SDK de OpenAI no esta instalado.") from exc

    client = AsyncOpenAI(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
        timeout=settings.ai_timeout_seconds,
        max_retries=1,
        default_headers=settings.ai_extra_headers,
    )

    try:
        response = await client.chat.completions.create(
            model=settings.ai_model,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "system", "content": system}, *messages],  # type: ignore[arg-type]
        )
    except AuthenticationError as exc:
        raise ApiError(
            "AI_UNAVAILABLE",
            f"El proveedor rechazo la credencial. Revisa {settings.ai_key_variable}.",
        ) from exc
    except RateLimitError as exc:
        raise ApiError(
            "AI_UNAVAILABLE", "El asistente esta saturado. Reintenta en un momento."
        ) from exc
    except (APITimeoutError, APIConnectionError, APIStatusError) as exc:
        logger.warning("Fallo de %s en chat: %s", settings.ai_provider, type(exc).__name__)
        raise ApiError("AI_UNAVAILABLE", "El asistente no esta disponible ahora mismo.") from exc

    choice = response.choices[0] if response.choices else None
    text = (choice.message.content or "") if choice else ""
    usage = response.usage
    return (
        text,
        getattr(usage, "prompt_tokens", 0) or 0,
        getattr(usage, "completion_tokens", 0) or 0,
    )
