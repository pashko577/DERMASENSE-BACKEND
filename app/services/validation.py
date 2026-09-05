"""Evidencia de validacion: cuanto se equivoca el modelo, medido.

Esta es la capa que el README principal llama "Machine Learning", y lo honesto
que puede decir hoy es esto: **Potts-Guy no fue superado por un modelo
entrenado** (ver README §5), asi que lo valioso no es una prediccion nueva sino
saber *cuanto vale* la que ya existe.

Tres numeros, en orden de utilidad creciente:

1. **Error global** sobre los compuestos medidos. Util, pero promedia moleculas
   que no se parecen en nada a la que el usuario tiene delante.
2. **Suelo de ruido**: la dispersion entre laboratorios para un mismo compuesto.
   Ningun modelo puede bajar de ahi, asi que es el listado contra el que se mide
   cualquier mejora.
3. **Error local**: el error del modelo sobre los compuestos *parecidos* al
   consultado. Es lo que de verdad responde "¿me puedo fiar de este numero para
   esta molecula?", y suele ser bastante mas estrecho que el global.

El tercero es el que hace util esta capa en una pantalla de formulacion.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

# Artefacto versionado que produce scripts/build_dataset.py. `app/` no importa
# de `ml/` (README §2): lo que cruza esa frontera es este archivo, no el modulo
# de entrenamiento.
REFERENCE_SET = Path(__file__).resolve().parent.parent / "data" / "validation_kp.csv"

# Potts & Guy (1992). Identica a packages/engine/qspr.ts: si divergen, el
# backend y el motor darian numeros distintos para la misma molecula.
POTTS_GUY_INTERCEPT = -2.7
POTTS_GUY_LOGP = 0.71
POTTS_GUY_MW = -0.0061

# Dominio de aplicabilidad declarado en docs/SIMULATION_MODEL.md.
MAX_MW = 500.0
MIN_LOGP = -1.0
MAX_LOGP = 6.0

# Cuantos vecinos definen el error local. Con menos de 5 la estimacion es ruido;
# con muchos mas deja de ser "local" y converge al error global.
NEIGHBOUR_COUNT = 8

# Escala para que MW y logP pesen parecido en la distancia. 100 g/mol de
# diferencia pesan como 1 unidad de logP: sin normalizar, el peso molecular
# domina y "parecido" acaba significando solo "de tamano similar".
MW_SCALE = 100.0

# Distancia maxima para que un vecino cuente como "parecido": 1.5 equivale a
# 150 g/mol de diferencia, o 1.5 unidades de logP, o una combinacion. Mas alla
# de eso el error local deja de decir nada sobre la molecula consultada.
MAX_NEIGHBOUR_DISTANCE = 1.5

DISCLAIMER = (
    "El error se mide contra permeabilidades experimentales publicadas. La "
    "prediccion sigue siendo una estimacion bajo los supuestos del modelo, no "
    "una medida de esta formulacion."
)


def potts_guy(molecular_weight: float, log_p: float) -> float:
    """log Kp = -2.7 + 0.71 logP - 0.0061 MW  (Kp en cm/h)."""
    return POTTS_GUY_INTERCEPT + POTTS_GUY_LOGP * log_p + POTTS_GUY_MW * molecular_weight


def check_domain(molecular_weight: float, log_p: float) -> tuple[bool, list[str]]:
    """Dominio de aplicabilidad, evaluado sobre el logP que recibe.

    Importa *que* logP se pasa. Para la cafeina, RDKit (Crippen) calcula -1.03 y
    PubChem publica -0.07: el primero la deja fuera del dominio y el segundo
    dentro. Misma molecula, veredictos opuestos. Por eso esta funcion no calcula
    descriptores por su cuenta: usa los que ya viajan con el ingrediente, que son
    los mismos que alimentan al motor.
    """
    reasons: list[str] = []
    if molecular_weight > MAX_MW:
        reasons.append(
            f"Peso molecular {molecular_weight:g} g/mol excede el limite de {MAX_MW:g} Da "
            "para penetracion cutanea (regla de Bos y Meinardi)."
        )
    if log_p < MIN_LOGP:
        reasons.append(
            f"logP {log_p:g} por debajo del dominio de Potts-Guy "
            f"({MIN_LOGP:g} a {MAX_LOGP:g}): compuesto muy hidrofilico."
        )
    if log_p > MAX_LOGP:
        reasons.append(
            f"logP {log_p:g} por encima del dominio de Potts-Guy "
            f"({MIN_LOGP:g} a {MAX_LOGP:g}): compuesto muy lipofilico."
        )
    return not reasons, reasons


@dataclass(frozen=True, slots=True)
class MeasuredCompound:
    name: str
    molecular_weight: float
    log_p: float
    log_kp_measured: float
    source: str

    @property
    def predicted(self) -> float:
        return potts_guy(self.molecular_weight, self.log_p)

    @property
    def absolute_error(self) -> float:
        return abs(self.predicted - self.log_kp_measured)

    def distance_to(self, molecular_weight: float, log_p: float) -> float:
        dm = (self.molecular_weight - molecular_weight) / MW_SCALE
        dl = self.log_p - log_p
        return math.hypot(dm, dl)


def parse_rows(rows: list[dict[str, Any]]) -> list[MeasuredCompound]:
    compounds: list[MeasuredCompound] = []
    for row in rows:
        try:
            compounds.append(
                MeasuredCompound(
                    name=str(row["compound_name"]),
                    molecular_weight=float(row["molecular_weight"]),
                    log_p=float(row["log_p"]),
                    log_kp_measured=float(row["log_kp_measured"]),
                    source=str(row.get("source") or ""),
                )
            )
        except (KeyError, TypeError, ValueError):
            # Una fila corrupta no debe invalidar el resto de la evidencia.
            continue
    return compounds


@lru_cache(maxsize=1)
def load_reference_set() -> tuple[MeasuredCompound, ...]:
    """Conjunto de referencia, leido del artefacto y cacheado.

    Se sirve desde disco y no desde `validation_records` a proposito: son datos
    publicados y citables, iguales para todo el mundo, asi que exigir sesion
    para consultarlos no protegeria nada y dejaria la pantalla de formulacion
    vacia para quien no ha entrado. La tabla sigue existiendo para consultas
    SQL y para el reporte Excel.
    """
    if not REFERENCE_SET.is_file():  # pragma: no cover - depende del despliegue
        return ()
    with REFERENCE_SET.open(encoding="utf-8", newline="") as handle:
        return tuple(parse_rows(list(csv.DictReader(handle))))


def _errors(compounds: list[MeasuredCompound]) -> tuple[float, float]:
    """MAE y RMSE de Potts-Guy sobre los compuestos dados."""
    if not compounds:
        return 0.0, 0.0
    errors = [c.absolute_error for c in compounds]
    mae = sum(errors) / len(errors)
    rmse = math.sqrt(sum(e * e for e in errors) / len(errors))
    return mae, rmse


def _normalize(value: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFD", value.strip().lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def evaluate(
    compounds: list[MeasuredCompound],
    *,
    molecular_weight: float,
    log_p: float,
    name: str | None = None,
    inci_name: str | None = None,
    neighbours: int = NEIGHBOUR_COUNT,
) -> dict[str, Any]:
    """Construye la evidencia para una molecula concreta."""
    predicted = potts_guy(molecular_weight, log_p)
    in_domain, reasons = check_domain(molecular_weight, log_p)

    global_mae, global_rmse = _errors(compounds)

    # Coincidencia exacta por nombre: el caso ideal, aunque raro. Un catalogo
    # cosmetico y un conjunto de permeabilidad se solapan poco.
    exact = None
    if compounds:
        wanted = {_normalize(n) for n in (name, inci_name) if n}
        for compound in compounds:
            if _normalize(compound.name) in wanted:
                exact = compound
                break

    nearest = sorted(compounds, key=lambda c: c.distance_to(molecular_weight, log_p))[:neighbours]
    local_mae, local_rmse = _errors(nearest)

    # Los "vecinos" siempre existen —siempre hay un compuesto menos lejano que
    # los demas—, pero eso no los hace parecidos. Para el acido hialuronico
    # (MW 5000) el mas cercano del conjunto esta a 45 unidades de distancia, y
    # dar un error local a partir de ahi seria inventar precision. Se mide la
    # distancia y se dice cuando no representa nada.
    max_distance = (
        max(c.distance_to(molecular_weight, log_p) for c in nearest) if nearest else float("inf")
    )
    representative = max_distance <= MAX_NEIGHBOUR_DISTANCE

    return {
        "prediction": {
            "log_kp": round(predicted, 3),
            "permeability_cm_h": round(10**predicted, 8),
            "model": "Potts & Guy (1992)",
            "formula": "log Kp = -2.7 + 0.71 logP - 0.0061 MW",
        },
        "domain": {"in_domain": in_domain, "reasons": reasons},
        "evidence": {
            "n_measured": len(compounds),
            "global_mae": round(global_mae, 3),
            "global_rmse": round(global_rmse, 3),
            "local_mae": round(local_mae, 3) if representative else None,
            "local_rmse": round(local_rmse, 3) if representative else None,
            "n_neighbours": len(nearest),
            "neighbours_representative": representative,
            "max_neighbour_distance": round(max_distance, 2) if nearest else None,
            "exact_match": (
                {
                    "compound_name": exact.name,
                    "log_kp_measured": round(exact.log_kp_measured, 3),
                    "log_kp_predicted": round(exact.predicted, 3),
                    "absolute_error": round(exact.absolute_error, 3),
                    "source": exact.source,
                }
                if exact
                else None
            ),
            "neighbours": [
                {
                    "compound_name": c.name,
                    "molecular_weight": c.molecular_weight,
                    "log_p": c.log_p,
                    "log_kp_measured": round(c.log_kp_measured, 3),
                    "log_kp_predicted": round(c.predicted, 3),
                    "absolute_error": round(c.absolute_error, 3),
                }
                for c in nearest
            ],
        },
        "disclaimer": DISCLAIMER,
    }
