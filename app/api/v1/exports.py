"""Exportacion de una simulacion al libro Excel de 7 hojas."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import Response

from app.api.v1.reports import load_simulation
from app.deps import CurrentUserDep, DbDep, rate_limit
from app.schemas.report import RegulatoryCheckRequest
from app.services import regulatory_rules
from app.services.excel import build_workbook

router = APIRouter(prefix="/exports", tags=["exportacion"])

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _safe_filename(title: str, simulation_id: str) -> str:
    """Nombre de archivo sin caracteres que rompan el encabezado o el sistema."""
    allowed = [char if char.isalnum() or char in " -_" else "_" for char in title]
    cleaned = "".join(allowed).strip().replace(" ", "_")[:60] or "simulacion"
    return f"dermasense_{cleaned}_{simulation_id[:8]}.xlsx"


@router.get(
    "/{simulation_id}.xlsx",
    summary="Libro Excel de 7 hojas con la simulacion completa",
    response_class=Response,
    responses={200: {"content": {_XLSX_MEDIA_TYPE: {}}}},
)
async def export_simulation(
    db: DbDep,
    user: CurrentUserDep,
    simulation_id: Annotated[str, Path(min_length=1, max_length=64)],
    area_cm2: Annotated[float, Query(gt=0, le=20000)] = 10.0,
    include_regulatory: Annotated[bool, Query()] = True,
    _: None = Depends(rate_limit(20, 60.0)),
) -> Response:
    record = await load_simulation(db, user, simulation_id)

    # El reporte de IA es opcional: si no existe, la hoja 6 lo dice en lugar de
    # fallar. La exportacion no puede depender de que el proveedor de IA este
    # disponible.
    report_row = await db.select_one(
        "ai_reports",
        columns="content",
        filters={"simulation_id": f"eq.{simulation_id}"},
    )
    report_content = (report_row or {}).get("content")

    regulatory = None
    if include_regulatory:
        regulatory = regulatory_rules.check(
            RegulatoryCheckRequest(
                ingredient_name=record.input_snapshot.ingredient.name,
                inci_name=record.input_snapshot.ingredient.inci_name,
                concentration_pct=record.concentration_pct,
            )
        )

    content = build_workbook(
        record,
        report_content=report_content,
        regulatory=regulatory,
        area_cm2=area_cm2,
    )

    return Response(
        content=content,
        media_type=_XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_safe_filename(record.title, record.id)}"'
            )
        },
    )
