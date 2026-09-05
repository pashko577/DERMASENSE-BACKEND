"""Catalogo de activos y resolucion contra PubChem."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.deps import CurrentUserDep, DbDep, rate_limit
from app.schemas.ingredient import (
    IngredientListResponse,
    IngredientRecord,
    ResolveRequest,
    ResolveResponse,
)
from app.services.pubchem import RESOLVE_NOTE, PubChemClient

router = APIRouter(prefix="/ingredients", tags=["ingredientes"])

_COLUMNS = (
    "id,owner_id,name,inci_name,molecular_weight,log_p,pka,category,risk_flags,"
    "sources,data_level,max_use_concentration,regulation_ref,regulation_version,"
    "regulation_checked_at"
)


def get_pubchem(request: Request) -> PubChemClient:
    client: PubChemClient = request.app.state.pubchem
    return client


@router.get(
    "",
    response_model=IngredientListResponse,
    summary="Catalogo publico mas los ingredientes privados del usuario",
)
async def list_ingredients(
    db: DbDep,
    _user: CurrentUserDep,
    search: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> IngredientListResponse:
    # No hay filtro por propietario: la politica RLS
    # `ingredients_read_public_or_own` ya devuelve exactamente el catalogo
    # publico mas lo privado del solicitante. Repetir el filtro aqui daria la
    # falsa impresion de que la seguridad depende de esta linea.
    filters: dict[str, str] = {}
    if search:
        filters["name"] = f"ilike.*{search}*"

    rows = await db.select(
        "ingredients",
        columns=_COLUMNS,
        filters=filters,
        order="name.asc",
        limit=limit,
    )
    items = [IngredientRecord.model_validate(row) for row in rows]
    return IngredientListResponse(items=items, count=len(items))


@router.post(
    "/resolve",
    response_model=ResolveResponse,
    summary="Nombre → candidatos de PubChem con CID estable",
)
async def resolve_ingredient(
    payload: ResolveRequest,
    _user: CurrentUserDep,
    pubchem: Annotated[PubChemClient, Depends(get_pubchem)],
    __: None = Depends(rate_limit(20, 60.0)),
) -> ResolveResponse:
    candidates = await pubchem.resolve_by_name(payload.name, payload.limit)
    return ResolveResponse(query=payload.name, candidates=candidates, note=RESOLVE_NOTE)
