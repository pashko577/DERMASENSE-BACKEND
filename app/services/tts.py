"""Fachada de sintesis de voz.

La API de Claude no ofrece texto-a-voz, asi que hay que elegir proveedor. El
elegido por defecto es **el navegador**: `speechSynthesis` no tiene clave que
filtrar, no cruza la red y funciona sin conexion igual que el motor. Este
endpoint existe para que cambiar de proveedor no toque una linea del frontend.

La restriccion que no se negocia (AGENTS.md regla 3): el TTS **nunca genera
contenido**. Pronuncia texto que Claude interpreto a partir de numeros que
produjo el motor.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from app.config import Settings
from app.errors import ApiError


class SpeechSynthesizer(Protocol):
    """El contrato, no la implementacion."""

    async def synthesize(self, text: str, voice: str = "es-ES") -> bytes: ...


class BrowserSynthesizer:
    """No sintetiza: delega en el navegador.

    Devolver un 503 aqui no es una carencia, es la respuesta correcta cuando
    `TTS_PROVIDER=browser`: el audio se produce en el cliente con
    `speechSynthesis` y ningun byte necesita cruzar la red.
    """

    async def synthesize(self, text: str, voice: str = "es-ES") -> bytes:
        raise ApiError(
            "DEPENDENCY_UNAVAILABLE",
            "La sintesis de voz se realiza en el navegador con la Web Speech API.",
            {
                "provider": "browser",
                "hint": "Usa speechSynthesis en el cliente, o configura TTS_PROVIDER.",
            },
        )


class ElevenLabsSynthesizer:
    _VOICES = {"es-ES": "EXAVITQu4vr4xnSDxMaL"}
    _BASE_URL = "https://api.elevenlabs.io/v1/text-to-speech"

    def __init__(self, api_key: str, timeout_seconds: float = 30.0) -> None:
        self._api_key = api_key
        self._timeout = timeout_seconds

    async def synthesize(self, text: str, voice: str = "es-ES") -> bytes:
        voice_id = self._VOICES.get(voice, voice)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._BASE_URL}/{voice_id}",
                    headers={"xi-api-key": self._api_key, "Accept": "audio/mpeg"},
                    json={
                        "text": text,
                        "model_id": "eleven_multilingual_v2",
                        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                    },
                )
        except httpx.HTTPError as exc:
            raise ApiError("UPSTREAM_ERROR", "El proveedor de voz no respondio.") from exc

        if response.status_code >= 400:
            raise ApiError(
                "UPSTREAM_ERROR",
                "El proveedor de voz rechazo la peticion.",
                {"status": response.status_code},
            )
        return response.content


class AzureSynthesizer:
    _SSML = (
        '<speak version="1.0" xml:lang="{lang}">'
        '<voice xml:lang="{lang}" name="{name}">{text}</voice>'
        "</speak>"
    )
    _VOICES = {"es-ES": "es-ES-ElviraNeural", "es-MX": "es-MX-DaliaNeural"}

    def __init__(self, api_key: str, region: str, timeout_seconds: float = 30.0) -> None:
        self._api_key = api_key
        self._region = region
        self._timeout = timeout_seconds

    async def synthesize(self, text: str, voice: str = "es-ES") -> bytes:
        name = self._VOICES.get(voice, self._VOICES["es-ES"])
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        body = self._SSML.format(lang=voice, name=name, text=escaped)

        url = f"https://{self._region}.tts.speech.microsoft.com/cognitiveservices/v1"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    url,
                    headers={
                        "Ocp-Apim-Subscription-Key": self._api_key,
                        "Content-Type": "application/ssml+xml",
                        "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3",
                    },
                    content=body.encode("utf-8"),
                )
        except httpx.HTTPError as exc:
            raise ApiError("UPSTREAM_ERROR", "El proveedor de voz no respondio.") from exc

        if response.status_code >= 400:
            raise ApiError(
                "UPSTREAM_ERROR",
                "El proveedor de voz rechazo la peticion.",
                {"status": response.status_code},
            )
        return response.content


def build_synthesizer(settings: Settings) -> SpeechSynthesizer:
    if settings.tts_provider == "elevenlabs":
        return ElevenLabsSynthesizer(settings.tts_api_key or "")
    if settings.tts_provider == "azure":
        return AzureSynthesizer(settings.tts_api_key or "", settings.tts_azure_region or "")
    return BrowserSynthesizer()
