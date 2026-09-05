"""Asistente conversacional sobre la simulación en curso.

Misma regla que el resto de la capa de IA: **el modelo no calcula nada**. Recibe
las métricas ya resueltas por el motor del navegador y solo las interpreta
(AGENTS.md regla 3).

Público, como la vista previa del reporte: sin login la pantalla del laboratorio
no serviría para nada. Protegido por límite por IP.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.deps import public_rate_limit
from app.errors import ApiError
from app.prompts.chat_es import SYSTEM_PROMPT, build_context_block, wrap_question
from app.schemas.simulation import SimulationInput, SimulationMetrics
from app.services import chat as service

router = APIRouter(prefix="/chat", tags=["asistente"])

# Ventana corta de conversacion: el contexto pesado es el estado de la
# simulacion, que se reinyecta actualizado en cada turno. Guardar mas historial
# solo encarece la peticion sin mejorar la respuesta.
HISTORY_WINDOW = 8


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)
    input: SimulationInput
    metrics: SimulationMetrics
    current_time_hours: float | None = Field(default=None, ge=0, le=48)


class ChatResponse(BaseModel):
    content: str
    model: str
    input_tokens: int
    output_tokens: int


@router.post("", response_model=ChatResponse, summary="Pregunta sobre la simulación en curso")
async def post_chat(
    payload: ChatRequest,
    config: Annotated[Settings, Depends(get_settings)],
    _: None = Depends(public_rate_limit(30, 60.0)),
) -> ChatResponse:
    history = [m.model_dump() for m in payload.messages[-HISTORY_WINDOW:]]

    # El estado de la simulacion se adjunta al ultimo turno del usuario, no al
    # prompt de sistema: asi viaja actualizado en cada pregunta sin invalidar
    # nada de lo anterior.
    if history and history[-1]["role"] == "user":
        context = build_context_block(payload.input, payload.metrics, payload.current_time_hours)
        history[-1]["content"] = wrap_question(context, history[-1]["content"])

    text, input_tokens, output_tokens = await service.complete(config, SYSTEM_PROMPT, history)

    if not text.strip():
        raise ApiError("AI_UNAVAILABLE", "El asistente devolvio una respuesta vacia.")

    return ChatResponse(
        content=text,
        model=config.ai_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
