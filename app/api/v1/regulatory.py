"""Verificacion regulatoria preliminar, basada en reglas."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import CurrentUserDep, rate_limit
from app.schemas.report import RegulatoryCheckRequest, RegulatoryCheckResponse
from app.services import regulatory_rules

router = APIRouter(prefix="/regulatory", tags=["regulatorio"])


@router.post(
    "/check",
    response_model=RegulatoryCheckResponse,
    summary="Contrasta un activo y su concentracion contra las reglas cargadas",
)
async def check_regulatory(
    payload: RegulatoryCheckRequest,
    _user: CurrentUserDep,
    __: None = Depends(rate_limit(60, 60.0)),
) -> RegulatoryCheckResponse:
    # Evaluacion puramente local sobre los YAML: sin red, sin modelo, sin
    # inferencia. La ausencia de una regla nunca se traduce como aprobacion.
    return regulatory_rules.check(payload)
