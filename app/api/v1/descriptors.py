"""SMILES → descriptores moleculares."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import CurrentUserDep, rate_limit
from app.schemas.ingredient import DescriptorsRequest, DescriptorsResponse
from app.services.rdkit_descriptors import compute_descriptors

router = APIRouter(prefix="/descriptors", tags=["descriptores"])


@router.post(
    "",
    response_model=DescriptorsResponse,
    summary="Calcula descriptores moleculares a partir de un SMILES",
)
async def post_descriptors(
    payload: DescriptorsRequest,
    _user: CurrentUserDep,
    __: None = Depends(rate_limit(60, 60.0)),
) -> DescriptorsResponse:
    # RDKit es sincrono y rapido (milisegundos para una molecula pequena), asi
    # que no justifica salir del hilo del bucle de eventos.
    return compute_descriptors(payload.smiles)
