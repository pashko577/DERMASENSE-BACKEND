"""Espejo Pydantic de `packages/engine/types.ts`.

Este archivo no define el modelo: lo *lee*. El motor vive en el navegador
(ADR-001) y estos esquemas existen para validar lo que ya calculo, no para
recalcularlo. Si un campo cambia en `types.ts`, cambia aqui; nunca al reves.

Los nombres viajan en camelCase porque asi los serializa el motor y asi quedan
guardados en `simulations.metrics` (jsonb). En Python se leen en snake_case;
`populate_by_name` permite construirlos de las dos formas.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

RiskFlag = Literal["retinoid", "aha", "bha", "surfactant", "essential_oil"]
LayerId = Literal["stratum_corneum", "viable_epidermis", "dermis", "hypodermis"]
Confidence = Literal["high", "medium", "low"]
IrritationBand = Literal["low", "moderate", "high", "very_high"]
DataLevel = Literal["verified", "literature", "estimated", "heuristic"]


class EngineModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class Ingredient(EngineModel):
    id: str | None = None
    name: str
    inci_name: str | None = None
    molecular_weight: float = Field(gt=0)
    log_p: float = Field(ge=-5, le=10)
    pka: float | None = None
    category: str | None = None
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    reference_threshold: float | None = None


class Vehicle(EngineModel):
    id: str | None = None
    name: str
    enhancer_factor: float = Field(ge=0.1, le=5.0)


class LayerProfile(EngineModel):
    layer: LayerId
    label: str | None = None
    thickness_um: float = Field(gt=0)
    diffusivity: float = Field(gt=0)
    elimination_rate: float = Field(ge=0)


class SkinModel(EngineModel):
    layers: list[LayerProfile]


class SimulationInput(EngineModel):
    ingredient: Ingredient
    vehicle: Vehicle
    concentration_pct: float = Field(gt=0, le=30)
    ph: float = Field(ge=3.0, le=9.0, alias="pH")
    duration_hours: float = Field(ge=1, le=48)
    applied_dose_mg_cm2: float = Field(gt=0)
    skin_model: SkinModel | None = None


class SimulationMetrics(EngineModel):
    """Salida del motor. Ningun valor de aqui se calcula en este servicio."""

    log_kp: float = Field(alias="logKp")
    permeability_cm_h: float = Field(alias="permeabilityCmH")
    max_flux_infinite_dose: float
    lag_time_hours: float
    absorbed_fraction_pct: float
    time_to50_pct_hours: float = Field(alias="timeTo50PctHours")
    penetration_depth_um: float
    peak_concentration_ve: float = Field(alias="peakConcentrationVE")
    irritation_index: float
    irritation_band: IrritationBand
    confidence: Confidence
    out_of_domain_reasons: list[str] = Field(default_factory=list)


class MassBalance(EngineModel):
    applied_ug_cm2: float
    remaining_in_vehicle_ug_cm2: float
    in_skin_ug_cm2: float
    eliminated_ug_cm2: float
    through_base_ug_cm2: float
    relative_error: float


class SimulationRecord(BaseModel):
    """Fila de `public.simulations` tal como la devuelve PostgREST."""

    model_config = ConfigDict(extra="ignore")

    id: str
    user_id: str
    title: str = "Simulacion sin titulo"
    ingredient_id: str | None = None
    vehicle_id: str | None = None
    skin_model_id: str | None = None
    concentration_pct: float
    ph: float
    duration_hours: float
    applied_dose_mg_cm2: float
    input_snapshot: SimulationInput
    metrics: SimulationMetrics
    engine_version: str
    notes: str | None = None
    created_at: str | None = None
