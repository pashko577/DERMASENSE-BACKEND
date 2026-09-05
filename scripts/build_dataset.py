"""Construye el conjunto de entrenamiento de permeabilidad a partir de HuskinDB.

**De donde salen los numeros.** HuskinDB es una recopilacion publicada y
revisada de mediciones de permeacion en **piel humana**, con la referencia y el
DOI de cada valor. Es de acceso abierto y descargable, asi que ningun numero de
este pipeline es inventado ni sintetico: cada fila se puede rastrear hasta su
publicacion original.

    Frohlich et al. (2020), "HuskinDB, a database for skin permeation of
    xenobiotics", Scientific Data 7, 414.  https://doi.org/10.1038/s41597-020-00764-z
    Datos: https://osf.io/26hdm/

**Por que HuskinDB y no Flynn.** El README hablaba del conjunto de Flynn (1990),
que es el clasico del que se derivo Potts-Guy pero no esta publicado en un
formato abierto y descargable. HuskinDB cubre el mismo terreno con mas
compuestos, procedencia por medicion y licencia abierta. Entrenar con datos que
un revisor puede descargar y verificar vale mas que citar un conjunto que nadie
puede comprobar.

**Tres transformaciones, todas explicitas:**

1. `logkp` viene en **cm/s** y Potts-Guy trabaja en **cm/h**: se suma
   log10(3600) = 3.5563.
2. Los descriptores (MW, logP) **no vienen en el archivo**: se calculan desde el
   SMILES con RDKit. Eso los vuelve `estimated`, no `verified`, y el modelo
   entrenado hereda ese nivel.
3. Un mismo compuesto aparece varias veces con condiciones experimentales
   distintas. Se agrega por **mediana** y se conserva la dispersion: si un
   compuesto varia 2 unidades logaritmicas entre laboratorios, ningun modelo va
   a predecirlo mejor que eso, y hay que poder decirlo.

Uso:
    python scripts/build_dataset.py
    python scripts/build_dataset.py --keep-out-of-domain
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.rdkit_descriptors import compute_descriptors  # noqa: E402

SOURCE_URL = "https://osf.io/download/4b68u/"
CACHE = Path(".cache/huskindb.csv")
OUTPUT = Path("ml/datasets/huskindb_kp.csv")

CITATION = (
    "Frohlich et al. (2020) Scientific Data 7:414, doi:10.1038/s41597-020-00764-z"
)

# log10(3600): pasa de cm/s (como publica HuskinDB) a cm/h (como usa Potts-Guy).
SECONDS_PER_HOUR_LOG = math.log10(3600)

# Dominio de aplicabilidad declarado en docs/SIMULATION_MODEL.md.
MAX_MW = 500.0
MIN_LOGP, MAX_LOGP = -1.0, 6.0


def download() -> Path:
    if CACHE.is_file():
        print(f"usando copia en cache: {CACHE}")
        return CACHE
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    print(f"descargando HuskinDB desde {SOURCE_URL}")
    urllib.request.urlretrieve(SOURCE_URL, CACHE)
    return CACHE


def main() -> int:
    parser = argparse.ArgumentParser(description="Construye el dataset de permeabilidad.")
    parser.add_argument(
        "--keep-out-of-domain",
        action="store_true",
        help="No filtra por el dominio de aplicabilidad (MW<=500, logP en [-1,6])",
    )
    args = parser.parse_args()

    path = download()

    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    print(f"{len(rows)} mediciones en el archivo de origen")

    # ── Agregacion por compuesto ────────────────────────────────────────────
    by_compound: dict[str, dict[str, object]] = {}
    skipped_no_value = 0

    for row in rows:
        name = (row.get("Compound name") or "").strip()
        smiles = (row.get("Smiles") or "").strip()
        raw = (row.get("logkp (cm/s)") or "").strip()
        if not name or not smiles or not raw:
            skipped_no_value += 1
            continue
        try:
            log_kp_cm_s = float(raw)
        except ValueError:
            skipped_no_value += 1
            continue

        entry = by_compound.setdefault(
            name,
            {
                "smiles": smiles,
                "values": [],
                "references": set(),
                "dois": set(),
            },
        )
        entry["values"].append(log_kp_cm_s + SECONDS_PER_HOUR_LOG)  # type: ignore[union-attr]
        if row.get("reference"):
            entry["references"].add(row["reference"].strip())  # type: ignore[union-attr]
        if row.get("DOI"):
            entry["dois"].add(row["DOI"].strip())  # type: ignore[union-attr]

    print(f"{len(by_compound)} compuestos unicos ({skipped_no_value} filas sin valor utilizable)")

    # ── Descriptores con RDKit ──────────────────────────────────────────────
    records: list[dict[str, object]] = []
    failed_smiles = 0
    out_of_domain = 0

    for name, entry in sorted(by_compound.items()):
        try:
            descriptors = compute_descriptors(str(entry["smiles"]))
        except Exception:  # noqa: BLE001 - un SMILES roto no debe abortar el lote
            failed_smiles += 1
            continue

        values: list[float] = entry["values"]  # type: ignore[assignment]
        median = statistics.median(values)
        spread = (max(values) - min(values)) if len(values) > 1 else 0.0

        in_domain = (
            descriptors.molecular_weight <= MAX_MW
            and MIN_LOGP <= descriptors.log_p <= MAX_LOGP
        )
        if not in_domain:
            out_of_domain += 1
            if not args.keep_out_of_domain:
                continue

        records.append(
            {
                "compound_name": name,
                "smiles": descriptors.canonical_smiles,
                "molecular_weight": round(descriptors.molecular_weight, 2),
                "log_p": round(descriptors.log_p, 3),
                "tpsa": round(descriptors.tpsa, 2),
                "h_bond_donors": descriptors.h_bond_donors,
                "h_bond_acceptors": descriptors.h_bond_acceptors,
                "log_kp_measured": round(median, 3),
                "n_measurements": len(values),
                "measurement_spread": round(spread, 3),
                "in_domain": int(in_domain),
                "source": CITATION,
                "doi": "; ".join(sorted(entry["dois"]))[:200],  # type: ignore[arg-type]
            }
        )

    print(f"{failed_smiles} SMILES que RDKit no pudo interpretar")
    print(f"{out_of_domain} compuestos fuera del dominio de aplicabilidad")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)

    print(f"\nescrito {OUTPUT} con {len(records)} compuestos")

    replicated = [r for r in records if r["n_measurements"] > 1]  # type: ignore[operator]
    if replicated:
        spreads = [float(r["measurement_spread"]) for r in replicated]
        print(
            f"{len(replicated)} compuestos con medicion repetida; "
            f"dispersion mediana entre laboratorios: {statistics.median(spreads):.2f} "
            "unidades de log Kp"
        )
        print(
            "Esa dispersion es el suelo de error: ningun modelo puede predecir\n"
            "mejor que la variabilidad de los propios datos experimentales."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
