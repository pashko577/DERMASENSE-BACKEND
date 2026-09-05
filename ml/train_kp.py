"""Entrena un modelo de permeabilidad cutanea y lo mide contra Potts-Guy.

Este archivo **no se importa nunca desde `app/`**, ni directa ni indirectamente.
Espeja la regla de `packages/engine`: entrenamiento y servicio no se mezclan. Lo
que `app/` podria consumir algun dia es el artefacto versionado que produce este
script, nunca el script.

── Por que ridge y no algo mas potente ──────────────────────────────────────

El conjunto tiene ~230 compuestos. Con ese tamano un RandomForest sobreajusta y
devuelve un R^2 excelente que no predice nada fuera de la muestra. Lo defendible
es una regresion lineal regularizada sobre pocos descriptores, validada
**leave-one-out**, publicando MAE y RMSE junto a cada prediccion.

── El liston, y por que es alto ─────────────────────────────────────────────

No basta con que el modelo gane a Potts-Guy: tiene que ganarle **por mas que el
ruido de los propios datos**. HuskinDB mide ~0.96 unidades de log Kp de
dispersion mediana entre laboratorios para el mismo compuesto. Una mejora de
0.05 unidades sobre una correlacion publicada de 1992, en datos que varian 0.96
entre si, no es una mejora: es ruido con buena presentacion.

Si el modelo no supera ese liston, la respuesta correcta es seguir usando
Potts-Guy y decirlo. Eso es lo que hace este script.

Uso:
    python scripts/build_dataset.py     # primero, construye el conjunto
    python ml/train_kp.py
    python ml/train_kp.py --features mw logp tpsa hbd hba
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_DATASET = Path("ml/datasets/huskindb_kp.csv")
DEFAULT_OUTPUT = Path("ml/artifacts")

# Potts & Guy (1992): la linea base a batir.
POTTS_GUY_INTERCEPT = -2.7
POTTS_GUY_LOGP = 0.71
POTTS_GUY_MW = -0.0061

FEATURE_COLUMNS = {
    "mw": "molecular_weight",
    "logp": "log_p",
    "tpsa": "tpsa",
    "hbd": "h_bond_donors",
    "hba": "h_bond_acceptors",
}


def potts_guy(molecular_weight: float, log_p: float) -> float:
    """log Kp = -2.7 + 0.71 logP - 0.0061 MW  (Kp en cm/h)."""
    return POTTS_GUY_INTERCEPT + POTTS_GUY_LOGP * log_p + POTTS_GUY_MW * molecular_weight


def main() -> int:  # noqa: C901 - un guion lineal se lee mejor entero
    parser = argparse.ArgumentParser(description="Entrena un modelo de log Kp.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--features",
        nargs="+",
        default=["mw", "logp"],
        choices=sorted(FEATURE_COLUMNS),
        help="Descriptores de entrada (por defecto los mismos que Potts-Guy)",
    )
    args = parser.parse_args()

    if not args.dataset.is_file():
        print(
            f"No existe {args.dataset}.\n\n"
            "Construyelo primero con datos publicados y citables:\n"
            "    python scripts/build_dataset.py\n\n"
            "Ese script descarga HuskinDB (mediciones en piel humana, acceso\n"
            "abierto, DOI por medicion) y calcula los descriptores con RDKit.",
            file=sys.stderr,
        )
        return 1

    try:
        import numpy as np
        import pandas as pd
        from sklearn.linear_model import RidgeCV
        from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
        from sklearn.model_selection import LeaveOneOut, cross_val_predict
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        print(
            "Faltan dependencias de entrenamiento. Son deliberadamente opcionales,\n"
            "porque el servicio no las necesita para funcionar:\n"
            "    pip install scikit-learn pandas",
            file=sys.stderr,
        )
        return 1

    frame = pd.read_csv(args.dataset)
    columns = [FEATURE_COLUMNS[name] for name in args.features]

    missing = [c for c in [*columns, "log_kp_measured"] if c not in frame.columns]
    if missing:
        print(f"El dataset no trae las columnas: {', '.join(missing)}", file=sys.stderr)
        return 1

    frame = frame.dropna(subset=[*columns, "log_kp_measured"])
    features = frame[columns].to_numpy(dtype=float)
    target = frame["log_kp_measured"].to_numpy(dtype=float)

    print("=" * 74)
    print("  Modelo de permeabilidad cutanea — log Kp (cm/h)")
    print("=" * 74)
    print(f"  dataset      : {args.dataset}")
    print(f"  compuestos   : {len(target)}")
    print(f"  descriptores : {', '.join(args.features)}")
    print(f"  validacion   : leave-one-out ({len(target)} pliegues)")

    # ── El suelo de error ───────────────────────────────────────────────────
    noise_floor = None
    if "measurement_spread" in frame.columns:
        spreads = frame.loc[frame.get("n_measurements", 0) > 1, "measurement_spread"]
        if len(spreads):
            noise_floor = float(statistics.median(spreads.tolist()))
            print(f"  suelo de ruido: {noise_floor:.2f} unidades (dispersion entre laboratorios)")

    # ── Entrenamiento ───────────────────────────────────────────────────────
    model = make_pipeline(StandardScaler(), RidgeCV(alphas=np.logspace(-3, 3, 25)))
    predicted = cross_val_predict(model, features, target, cv=LeaveOneOut())
    base_inputs = frame[["molecular_weight", "log_p"]].to_numpy()
    baseline = np.array([potts_guy(row[0], row[1]) for row in base_inputs])

    def scores(values: np.ndarray) -> dict[str, float]:
        return {
            "mae": float(mean_absolute_error(target, values)),
            "rmse": float(mean_squared_error(target, values) ** 0.5),
            "r2": float(r2_score(target, values)),
        }

    ridge_scores = scores(predicted)
    baseline_scores = scores(baseline)

    print("\n" + "-" * 74)
    print(f"  {'modelo':<28} {'MAE':>8} {'RMSE':>8} {'R2':>8}")
    print("-" * 74)
    for label, s in (
        (f"Ridge LOO ({'+'.join(args.features)})", ridge_scores),
        ("Potts-Guy (1992)", baseline_scores),
    ):
        print(f"  {label:<28} {s['mae']:>8.3f} {s['rmse']:>8.3f} {s['r2']:>8.3f}")
    print("-" * 74)

    improvement = baseline_scores["mae"] - ridge_scores["mae"]
    print(f"\n  mejora en MAE sobre Potts-Guy: {improvement:+.3f} unidades de log Kp")

    # ── El veredicto ────────────────────────────────────────────────────────
    beats_baseline = improvement > 0
    beats_noise = noise_floor is not None and improvement > noise_floor
    verdict: str

    if not beats_baseline:
        verdict = "no_supera_la_linea_base"
        print(
            "\n  VEREDICTO: el modelo NO supera a Potts-Guy.\n"
            "  Sigue usandose la correlacion publicada. Este resultado es informacion\n"
            "  util, no un fracaso: dice que con estos descriptores no hay margen."
        )
    elif not beats_noise:
        verdict = "mejora_dentro_del_ruido"
        print(
            f"\n  VEREDICTO: el modelo mejora {improvement:.3f} unidades, pero los propios\n"
            f"  datos varian {noise_floor:.2f} unidades entre laboratorios para el mismo\n"
            "  compuesto. La mejora NO supera el ruido experimental, asi que no\n"
            "  justifica sustituir una correlacion publicada y citable.\n"
            "  El motor sigue con Potts-Guy."
        )
    else:
        verdict = "supera_linea_base_y_ruido"
        print(
            "\n  VEREDICTO: el modelo supera a Potts-Guy por encima del ruido experimental.\n"
            "  Candidato defendible; requiere validacion externa antes de entrar al motor."
        )

    # ── Artefacto ───────────────────────────────────────────────────────────
    model.fit(features, target)
    ridge = model.named_steps["ridgecv"]
    scaler = model.named_steps["standardscaler"]

    args.out.mkdir(parents=True, exist_ok=True)
    artifact = {
        "created_at": datetime.now(UTC).isoformat(),
        "dataset": str(args.dataset),
        "n_compounds": int(len(target)),
        "features": args.features,
        "validation": "leave-one-out",
        "alpha": float(ridge.alpha_),
        "coefficients": dict(zip(args.features, [float(c) for c in ridge.coef_], strict=True)),
        "intercept": float(ridge.intercept_),
        "scaler_mean": [float(v) for v in scaler.mean_],
        "scaler_scale": [float(v) for v in scaler.scale_],
        "metrics": {"ridge_loo": ridge_scores, "potts_guy": baseline_scores},
        "noise_floor_log_units": noise_floor,
        "verdict": verdict,
        "data_level": "estimated",
        "caveat": (
            "logP y descriptores calculados con RDKit (Crippen), no medidos. "
            "log Kp medido en piel humana, agregado por mediana entre condiciones "
            "experimentales heterogeneas."
        ),
        "in_production": False,
    }

    path = args.out / "kp_ridge.json"
    path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  artefacto: {path}")
    print(f"  in_production: {artifact['in_production']}  (el motor sigue con Potts-Guy)")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
