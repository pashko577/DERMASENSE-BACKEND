"""Reportes de IA, sintesis de voz y verificacion regulatoria."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.simulation import SimulationInput, SimulationMetrics

Jurisdiction = Literal["eu", "us"]
RuleOutcome = Literal["pass", "attention", "fail", "not_applicable", "unknown"]


class ReportRequest(BaseModel):
    """El texto libre del usuario es *dato de contexto*, nunca instruccion.

    El prompt de sistema lo dice explicitamente (AI_PROMPTS §2, regla 6) y aqui
    se acota el tamano para que no pueda desplazar al resto del contexto.
    """

    notes: str | None = Field(default=None, max_length=2000)
    force_regenerate: bool = False


class ReportPreviewRequest(BaseModel):
    """Reporte sobre una simulacion que todavia no se ha guardado.

    El motor corre en el navegador y produce `input` + `metrics` antes de que
    exista ninguna fila en `simulations`. El frontend pide el informe en ese
    momento, asi que necesita una via que no dependa de un `simulation_id`.

    No persiste nada: sin simulacion a la que colgarlo, no hay fila en
    `ai_reports`. Es una vista previa. El informe definitivo, el que queda
    guardado y cuenta para la cuota, sigue siendo `POST /reports/{id}`.
    """

    input: SimulationInput
    metrics: SimulationMetrics
    notes: str | None = Field(default=None, max_length=2000)


class ReportRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    simulation_id: str
    content: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    created_at: str | None = None


class VoiceRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice: str = "es-ES"
    format: Literal["mp3", "wav"] = "mp3"


class RegulatoryCheckRequest(BaseModel):
    ingredient_name: str = Field(min_length=1, max_length=200)
    inci_name: str | None = None
    cas_number: str | None = None
    concentration_pct: float = Field(gt=0, le=100)
    product_type: str = Field(default="leave_on")
    jurisdictions: list[Jurisdiction] = Field(default_factory=lambda: ["eu", "us"])


class RegulatoryFinding(BaseModel):
    jurisdiction: Jurisdiction
    regulation: str
    requirement: str
    outcome: RuleOutcome
    message: str
    source: str
    checked_at: str
    limit_pct: float | None = None


class RegulatoryCheckResponse(BaseModel):
    """Resultado *preliminar*, basado en reglas y fuentes citadas.

    No es una prediccion ni una validacion regulatoria. El README principal §18
    lo fija: capa basada en reglas y fuentes. Por eso cada hallazgo viaja con su
    cita y su fecha de verificacion, y el conjunto lleva un descargo explicito.
    """

    ingredient_name: str
    concentration_pct: float
    product_type: str
    findings: list[RegulatoryFinding]
    summary: RuleOutcome
    disclaimer: str
