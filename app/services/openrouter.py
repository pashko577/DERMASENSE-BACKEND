"""Cliente para proveedores con API compatible con OpenAI.

Cubre **OpenRouter** y **Groq** con el mismo codigo: los dos hablan
`/chat/completions`, asi que lo unico que cambia entre ellos es la URL base, la
clave y el identificador del modelo. Todo eso vive en `config.py`.

Que elegir, y por que importa aqui mas que en otros proyectos:

- **OpenRouter** sirve el mismo `claude-sonnet-5` que fija TRD §1, a la misma
  tarifa. La documentacion del proyecto sigue siendo cierta.
- **Groq** sirve modelos abiertos con latencia muy baja y capa gratuita. Es una
  alternativa valida, pero **cambia el modelo**: el prompt de sistema tiene seis
  reglas de honestidad —nunca declarar un producto seguro, tratar el indice de
  irritacion como heuristico— y un modelo distinto las respeta de forma
  distinta. Conviene revisar la salida antes de fiarse.

Lo que cambia frente a Anthropic es la forma de la API, no el contrato: estos
proveedores hablan `/chat/completions` y Anthropic habla su Messages API. Este
modulo absorbe la diferencia y expone exactamente el mismo `stream_report` que
`ClaudeService`, de modo que `reports.py` no sabe —ni necesita saber— por donde
sale la peticion.

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


class OpenAICompatibleService:
    """Mismo contrato que `ClaudeService`, distinto transporte."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self):  # noqa: ANN202 - el tipo depende del SDK instalado
        if not self._settings.ai_api_key:
            raise ApiError(
                "AI_UNAVAILABLE",
                "La generacion de reportes no esta disponible: falta "
                f"{self._settings.ai_key_variable}.",
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise ApiError(
                "AI_UNAVAILABLE",
                "El SDK de OpenAI (usado como cliente de OpenRouter) no esta instalado.",
            ) from exc

        return AsyncOpenAI(
            api_key=self._settings.ai_api_key,
            base_url=self._settings.ai_base_url,
            timeout=self._settings.ai_timeout_seconds,
            max_retries=1,
            # OpenRouter las usa para atribuir el trafico; Groq las ignora.
            default_headers=self._settings.ai_extra_headers,
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
            logger.error("%s rechazo la clave de API", self._settings.ai_provider)
            raise ApiError(
                "AI_UNAVAILABLE",
                "El proveedor de IA rechazo la credencial. Revisa "
                f"{self._settings.ai_key_variable}.",
            ) from exc
        except RateLimitError as exc:
            logger.warning("%s devolvio 429", self._settings.ai_provider)
            raise ApiError(
                "AI_UNAVAILABLE",
                "El servicio de IA esta saturado. Reintenta en unos minutos.",
            ) from exc
        except APITimeoutError as exc:
            logger.warning("%s excedio el tiempo limite", self._settings.ai_provider)
            raise ApiError(
                "AI_UNAVAILABLE",
                "El servicio de IA no respondio a tiempo. Puedes reintentar.",
            ) from exc
        except (APIConnectionError, APIStatusError) as exc:
            logger.warning("Fallo de %s: %s", self._settings.ai_provider, type(exc).__name__)
            raise ApiError(
                "AI_UNAVAILABLE",
                "El servicio de IA no esta disponible. Las metricas siguen guardadas.",
            ) from exc

        if not accounting.text.strip():
            raise ApiError("AI_UNAVAILABLE", "El servicio de IA devolvio una respuesta vacia.")


# Nombre anterior, conservado para no romper importaciones existentes.
OpenRouterService = OpenAICompatibleService
