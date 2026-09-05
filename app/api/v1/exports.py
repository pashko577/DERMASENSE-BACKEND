"""Exportacion de una simulacion al libro Excel de 7 hojas."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.v1.reports import load_simulation
from app.deps import CurrentUserDep, DbDep, public_rate_limit, rate_limit
from app.schemas.report import RegulatoryCheckRequest
from app.schemas.simulation import SimulationInput, SimulationMetrics, SimulationRecord
from app.services import regulatory_rules
from app.services.excel import build_workbook

router = APIRouter(prefix="/exports", tags=["exportacion"])

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _safe_filename(title: str, simulation_id: str) -> str:
    """Nombre de archivo sin caracteres que rompan el encabezado o el sistema."""
    allowed = [char if char.isalnum() or char in " -_" else "_" for char in title]
    cleaned = "".join(allowed).strip().replace(" ", "_")[:60] or "simulacion"
    return f"dermasense_{cleaned}_{simulation_id[:8]}.xlsx"


class ExportPreviewRequest(BaseModel):
    """Libro de una simulacion que todavia no se ha guardado.

    Mismo compromiso que la vista previa del reporte: el motor corre en el
    navegador y produce las metricas antes de que exista ninguna fila en
    `simulations`. Exigir sesion para descargar un Excel de datos que el propio
    usuario acaba de calcular en su maquina no protege nada.

    El libro sale igual de completo; lo unico que no lleva es el identificador
    de una simulacion guardada.
    """

    input: SimulationInput
    metrics: SimulationMetrics
    title: str | None = Field(default=None, max_length=200)
    report_content: str | None = Field(default=None, max_length=20000)
    area_cm2: float = Field(default=10.0, gt=0, le=20000)
    include_regulatory: bool = True


@router.post(
    "",
    summary="Libro Excel de una simulacion no guardada",
    response_class=Response,
    responses={200: {"content": {_XLSX_MEDIA_TYPE: {}}}},
)
async def export_preview(
    payload: ExportPreviewRequest,
    _: None = Depends(public_rate_limit(20, 60.0)),
) -> Response:
    ingredient = payload.input.ingredient
    title = payload.title or (
        f"{ingredient.name} {payload.input.concentration_pct}% en {payload.input.vehicle.name}"
    )

    # Se arma un registro en memoria con la misma forma que el de la base, para
    # que `build_workbook` no tenga que saber si la simulacion esta guardada.
    record = SimulationRecord(
        id="sin-guardar",
        user_id="sin-guardar",
        title=title,
        concentration_pct=payload.input.concentration_pct,
        ph=payload.input.ph,
        duration_hours=payload.input.duration_hours,
        applied_dose_mg_cm2=payload.input.applied_dose_mg_cm2,
        input_snapshot=payload.input,
        metrics=payload.metrics,
        engine_version="1.0.0",
        created_at=datetime.now(UTC).isoformat(),
    )

    regulatory = None
    if payload.include_regulatory:
        regulatory = regulatory_rules.check(
            RegulatoryCheckRequest(
                ingredient_name=ingredient.name,
                inci_name=ingredient.inci_name,
                concentration_pct=payload.input.concentration_pct,
            )
        )

    content = build_workbook(
        record,
        report_content=payload.report_content,
        regulatory=regulatory,
        area_cm2=payload.area_cm2,
    )

    return Response(
        content=content,
        media_type=_XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{_safe_filename(title, "preview")}"'
        },
    )


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
