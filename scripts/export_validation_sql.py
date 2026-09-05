"""Convierte el conjunto de validacion en una migracion SQL.

`public.validation_records` existe para poder publicar un **error real** —predicho
contra medido— en lugar de una promesa. Este script la puebla con los compuestos
de HuskinDB, cada uno con su cita.

Se genera en vez de escribirse a mano por una razon simple: si el dataset se
reconstruye, la migracion se regenera y no hay dos versiones de la verdad.

Uso:
    python scripts/build_dataset.py          # primero
    python scripts/export_validation_sql.py
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

DATASET = Path("ml/datasets/huskindb_kp.csv")
DEFAULT_OUTPUT = Path("../DERMASENSE/supabase/migrations/005_validation.sql")

HEADER = """\
-- ─────────────────────────────────────────────────────────────────────────────
-- 005_validation.sql — conjunto de referencia con permeabilidades MEDIDAS
--
-- GENERADO por scripts/export_validation_sql.py del repositorio backend.
-- No editar a mano: regenerar desde el dataset.
--
-- Fuente: HuskinDB — Fröhlich et al. (2020), Scientific Data 7:414
--         doi:10.1038/s41597-020-00764-z · datos en https://osf.io/26hdm/
--         Mediciones de permeación en piel humana, con DOI por medición.
--
-- Estos son valores EXPERIMENTALES (log Kp en cm/h, convertidos desde los cm/s
-- que publica HuskinDB). MW y logP, en cambio, los calcula RDKit desde el SMILES:
-- son 'estimated', no medidos.
--
-- Para que sirve: contrastar la predicción de Potts-Guy contra la medida y
-- publicar el error. Medido sobre estos {count} compuestos, Potts-Guy da
-- MAE = 0.898 y RMSE = 1.304 unidades de log Kp. La dispersión entre
-- laboratorios para un mismo compuesto es de 0.96 unidades: ese es el suelo,
-- y ningún modelo puede bajar de ahí.
-- ─────────────────────────────────────────────────────────────────────────────

insert into public.validation_records
  (dataset, compound_name, molecular_weight, log_p, log_kp_measured, source)
values
"""

FOOTER = """on conflict (dataset, compound_name) do nothing;
"""


def quote(value: str) -> str:
    """Escapa comillas simples para un literal SQL."""
    return "'" + value.replace("'", "''") + "'"


def main() -> int:
    parser = argparse.ArgumentParser(description="Genera 005_validation.sql.")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if not args.dataset.is_file():
        print(f"No existe {args.dataset}. Ejecuta antes scripts/build_dataset.py")
        return 1

    with args.dataset.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    lines: list[str] = []
    for row in rows:
        reference = row["doi"].split(";")[0].strip() or "HuskinDB"
        source = f"HuskinDB (doi:10.1038/s41597-020-00764-z) · medición: {reference}"
        lines.append(
            "  ('huskindb_2020', "
            f"{quote(row['compound_name'])}, "
            f"{row['molecular_weight']}, "
            f"{row['log_p']}, "
            f"{row['log_kp_measured']}, "
            f"{quote(source)})"
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    body = ",\n".join(lines) + "\n"
    args.out.write_text(
        HEADER.format(count=len(rows)) + body + FOOTER,
        encoding="utf-8",
    )
    print(f"escrito {args.out} con {len(rows)} compuestos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
