"""Verificacion regulatoria preliminar, basada en reglas.

**Publico, sin sesion.** Evalua archivos YAML versionados con su cita y su fecha:
no gasta dinero, no toca datos de usuario y no dice nada que no este ya en el
Diario Oficial. Cerrarlo tras un login solo esconderia informacion publica.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import public_rate_limit
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
    _: None = Depends(public_rate_limit(240, 60.0)),
) -> RegulatoryCheckResponse:
    # Evaluacion puramente local sobre los YAML: sin red, sin modelo, sin
    # inferencia. La ausencia de una regla nunca se traduce como aprobacion.
    return regulatory_rules.check(payload)
