"""Fachada de sintesis de voz."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.deps import CurrentUserDep, rate_limit
from app.schemas.report import VoiceRequest
from app.services.tts import SpeechSynthesizer

router = APIRouter(prefix="/voice", tags=["voz"])

_MEDIA_TYPES = {"mp3": "audio/mpeg", "wav": "audio/wav"}


def get_synthesizer(request: Request) -> SpeechSynthesizer:
    synthesizer: SpeechSynthesizer = request.app.state.tts
    return synthesizer


@router.post(
    "/speech",
    summary="Texto a audio (fachada; por defecto delega en el navegador)",
    response_class=Response,
)
async def synthesize_speech(
    payload: VoiceRequest,
    _user: CurrentUserDep,
    synthesizer: Annotated[SpeechSynthesizer, Depends(get_synthesizer)],
    __: None = Depends(rate_limit(30, 60.0)),
) -> Response:
    # El texto llega ya redactado: este endpoint no genera contenido, solo lo
    # pronuncia (AGENTS.md regla 3).
    audio = await synthesizer.synthesize(payload.text, payload.voice)
    return Response(content=audio, media_type=_MEDIA_TYPES[payload.format])
