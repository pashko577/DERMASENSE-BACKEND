"""Asistente de curacion de los ~60 activos del catalogo.

Por que un asistente y no un importador automatico: PubChem devuelve casi
siempre `XLogP3`, calculado por computadora. Un error de 0.5 unidades en logP
desplaza `log Kp` en 0.35, un factor de mas de 2 en permeabilidad. Importar en
masa produciria un catalogo grande y poco fiable; la decision de DATA_SOURCES
§3.2 fue al reves: pocos activos, cada valor revisado por una persona.

El script *propone*. Quien ejecuta decide, y esa decision queda escrita en el
campo `sources` de cada ingrediente.

Uso:
    python scripts/curate_ingredients.py --name "acido salicilico"
    python scripts/curate_ingredients.py --input ml/datasets/objetivo.txt --out catalogo.json

La salida es un JSON listo para revisar y cargar con `supabase db` o el editor
SQL. El script no escribe en la base de datos: la curacion se revisa antes de
entrar.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.pubchem import PubChemClient  # noqa: E402
from app.services.rdkit_descriptors import compute_descriptors  # noqa: E402


async def curate_one(client: PubChemClient, name: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": name,
        "status": "pendiente_de_revision",
        "candidates": [],
        "warnings": [],
    }

    try:
        candidates = await client.resolve_by_name(name, limit=3)
    except Exception as exc:  # noqa: BLE001 - el script no debe abortar por un activo
        entry["status"] = "error"
        entry["warnings"].append(f"PubChem: {exc}")
        return entry

    if not candidates:
        entry["warnings"].append("PubChem no devolvio candidatos; hay que curarlo a mano.")
        return entry

    for candidate in candidates:
        row = candidate.model_dump(mode="json")

        # Contraste util: si RDKit y PubChem discrepan mucho en logP, ninguno de
        # los dos merece entrar sin que una persona lo mire.
        if candidate.canonical_smiles:
            try:
                descriptors = compute_descriptors(candidate.canonical_smiles)
                row["rdkit"] = descriptors.model_dump(mode="json")
                if candidate.xlogp is not None:
                    difference = abs(descriptors.log_p - candidate.xlogp)
                    row["log_p_difference"] = round(difference, 2)
                    if difference > 1.0:
                        entry["warnings"].append(
                            f"CID {candidate.cid}: RDKit y PubChem difieren {difference:.2f} "
                            "unidades en logP. Revisa cual usar antes de curar."
                        )
            except Exception as exc:  # noqa: BLE001
                row["rdkit_error"] = str(exc)

        entry["candidates"].append(row)

    return entry


async def run(names: list[str], output: Path | None) -> int:
    client = PubChemClient()
    results = []

    for name in names:
        print(f"→ {name}", file=sys.stderr)
        results.append(await curate_one(client, name))

    document = {
        "note": (
            "Propuestas sin curar. Ningun campo esta verificado: revisa el logP en la "
            "ficha de PubChem, decide el valor y anota su procedencia en 'sources' "
            "antes de cargar nada."
        ),
        "ingredients": results,
    }

    payload = json.dumps(document, ensure_ascii=False, indent=2)
    if output:
        output.write_text(payload, encoding="utf-8")
        print(f"Escrito en {output}", file=sys.stderr)
    else:
        print(payload)

    pending = sum(1 for item in results if item["warnings"])
    if pending:
        print(f"\n{pending} activo(s) requieren atencion manual.", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Asistente de curacion del catalogo.")
    parser.add_argument("--name", action="append", default=[], help="Nombre del activo (repetible)")
    parser.add_argument("--input", type=Path, help="Archivo con un nombre por linea")
    parser.add_argument("--out", type=Path, help="Archivo JSON de salida")
    args = parser.parse_args()

    names: list[str] = list(args.name)
    if args.input:
        lineas = args.input.read_text(encoding="utf-8").splitlines()
        names.extend(linea.strip() for linea in lineas if linea.strip())

    if not names:
        parser.error("Indica al menos --name o --input.")

    return asyncio.run(run(names, args.out))


if __name__ == "__main__":
    raise SystemExit(main())
