"""Generacion del reporte tecnico con Claude, servido como SSE.

La propiedad que define este endpoint no es que genere texto, sino **como
falla**: ante cualquier problema del proveedor devuelve `503 AI_UNAVAILABLE` y no
escribe nada en `ai_reports`. Las metricas de la simulacion siguen visibles y
guardables. Es lo que verifica `tests/test_reports.py`, y es lo que hace que el
reporte sea aditivo en vez de una dependencia critica.

Detalle de implementacion que sostiene esa promesa: antes de devolver la
respuesta en streaming se consume el **primer fragmento**. Si el proveedor esta
caido, el fallo ocurre mientras todavia se puede emitir un 503 de verdad; si se
devolviera el stream de inmediato, el estado ya seria 200 y el error solo podria
viajar como un evento dentro del cuerpo, que es mucho mas facil de ignorar para
un cliente.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import StreamingResponse

from app.config import Settings, get_settings
from app.deps import CurrentUser, CurrentUserDep, DbDep, rate_limit
from app.errors import ApiError
from app.prompts.report_es import build_user_prompt
from app.schemas.report import ReportRequest
from app.schemas.simulation import SimulationRecord
from app.services.ai import ReportGenerator
from app.services.claude import StreamAccounting

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["reportes"])

_SIMULATION_COLUMNS = (
    "id,user_id,title,ingredient_id,vehicle_id,skin_model_id,concentration_pct,ph,"
    "duration_hours,applied_dose_mg_cm2,input_snapshot,metrics,engine_version,notes,created_at"
)


async def load_simulation(db: DbDep, user: CurrentUser, simulation_id: str) -> SimulationRecord:
    """Carga una simulacion propia, o falla con el codigo correcto.

    RLS ya impide leer la simulacion de otra persona, asi que la comprobacion de
    `user_id` de mas abajo nunca deberia dispararse en produccion. Se mantiene
    como defensa en profundidad: si algun dia una politica se relaja por error,
    este servicio no se convierte en el agujero por el que se filtran los datos
    de otro usuario.
    """
    row = await db.select_one(
        "simulations",
        columns=_SIMULATION_COLUMNS,
        filters={"id": f"eq.{simulation_id}"},
    )
    if row is None:
        raise ApiError("NOT_FOUND", "La simulacion no existe o no es accesible.")

    record = SimulationRecord.model_validate(row)
    if record.user_id != user.id:
        logger.error(
            "RLS no filtro la simulacion %s: pertenece a otro usuario", simulation_id
        )
        raise ApiError("FORBIDDEN", "La simulacion pertenece a otro usuario.")
    return record


async def _assert_quota(db: DbDep, settings: Settings) -> None:
    """Cuota real, contada contra la base de datos.

    Deliberadamente no se usa el limitador en memoria: no sobrevive a un
    reinicio ni se comparte entre replicas, y esta cuota protege el gasto.
    RLS ya restringe `ai_reports` a los del propio usuario, asi que basta con
    filtrar por fecha.
    """
    since = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
    used = await db.count("ai_reports", filters={"created_at": f"gte.{since}"})
    if used >= settings.ai_reports_daily_quota:
        raise ApiError(
            "RATE_LIMITED",
            f"Alcanzaste el limite de {settings.ai_reports_daily_quota} reportes en 24 horas.",
            {"used": used, "quota": settings.ai_reports_daily_quota},
        )


def _sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post(
    "/{simulation_id}",
    summary="Genera el reporte tecnico de una simulacion (text/event-stream)",
    response_class=StreamingResponse,
)
async def generate_report(
    request: Request,
    db: DbDep,
    user: CurrentUserDep,
    settings: Annotated[Settings, Depends(get_settings)],
    simulation_id: Annotated[str, Path(min_length=1, max_length=64)],
    payload: ReportRequest | None = None,
    _: None = Depends(rate_limit(10, 60.0)),
) -> StreamingResponse:
    body = payload or ReportRequest()

    if not settings.ai_enabled:
        raise ApiError(
            "AI_UNAVAILABLE",
            f"La generacion de reportes no esta configurada: falta {settings.ai_key_variable}.",
        )

    record = await load_simulation(db, user, simulation_id)

    existing = await db.select_one(
        "ai_reports",
        columns="id,simulation_id,created_at",
        filters={"simulation_id": f"eq.{simulation_id}"},
    )
    if existing is not None:
        if not body.force_regenerate:
            # `ai_reports.simulation_id` es unique: un reporte por simulacion.
            # Regenerar exige pedirlo de forma explicita (AI_PROMPTS §5).
            raise ApiError(
                "CONFLICT",
                "Esta simulacion ya tiene un reporte. Usa force_regenerate para rehacerlo.",
                {"report_id": existing.get("id")},
            )
        await db.delete("ai_reports", filters={"simulation_id": f"eq.{simulation_id}"})

    await _assert_quota(db, settings)

    prompt = build_user_prompt(record.input_snapshot, record.metrics, body.notes)
    service: ReportGenerator = request.app.state.claude
    accounting = StreamAccounting()
    stream = service.stream_report(prompt, accounting)

    # Se consume el primer fragmento aqui, fuera del generador de respuesta, para
    # que un proveedor caido produzca un 503 de verdad y no un 200 con un error
    # escondido en el cuerpo.
    try:
        first = await anext(stream)
    except StopAsyncIteration:
        raise ApiError(
            "AI_UNAVAILABLE", "El servicio de IA devolvio una respuesta vacia."
        ) from None

    async def emit() -> AsyncIterator[str]:
        yield _sse(
            "meta",
            {
                "simulation_id": record.id,
                "model": settings.ai_model,
                "engine_version": record.engine_version,
            },
        )
        yield _sse("delta", {"text": first})

        try:
            async for fragment in stream:
                yield _sse("delta", {"text": fragment})
        except ApiError as exc:
            # El estado HTTP ya es 200: lo unico honesto que queda es decirlo en
            # el cuerpo y no persistir nada.
            yield _sse("error", exc.payload()["error"])
            return

        try:
            saved = await db.insert(
                "ai_reports",
                {
                    "simulation_id": record.id,
                    "content": accounting.text,
                    "model": settings.ai_model,
                    "input_tokens": accounting.input_tokens,
                    "output_tokens": accounting.output_tokens,
                },
            )
        except ApiError as exc:
            yield _sse("error", exc.payload()["error"])
            return

        yield _sse(
            "done",
            {
                "report_id": (saved or {}).get("id"),
                "input_tokens": accounting.input_tokens,
                "output_tokens": accounting.output_tokens,
                "characters": len(accounting.text),
                # El frontend necesita poder avisar de que el texto quedo
                # cortado: es un 200 con contenido incompleto, no un error.
                "truncated": accounting.truncated,
            },
        )

    return StreamingResponse(
        emit(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Render y varios proxies bufferizan por defecto y anulan el
            # streaming: sin esto el usuario espera el bloque completo.
            "X-Accel-Buffering": "no",
        },
    )
