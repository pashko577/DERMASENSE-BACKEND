"""El modulo de ML: que no mienta sobre lo que sabe.

Estas pruebas no comprueban que el modelo sea bueno —no lo es, y ese es el
hallazgo—. Comprueban que el pipeline **declare la verdad**: que la formula base
sea la misma que usa el motor, que el artefacto se marque como no productivo, y
que `ml/` no se cuele en el camino de ejecucion del servicio.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "ml" / "datasets" / "huskindb_kp.csv"
ARTIFACT = ROOT / "ml" / "artifacts" / "kp_ridge.json"

REQUIRED_COLUMNS = {
    "compound_name",
    "smiles",
    "molecular_weight",
    "log_p",
    "log_kp_measured",
    "n_measurements",
    "measurement_spread",
    "source",
    "doi",
}


def test_ml_no_se_importa_desde_app() -> None:
    """La regla estructural: `app/` nunca depende de `ml/`.

    Se comprueba sobre el texto fuente en vez de con un import, porque lo que
    importa es que nadie escriba la dependencia, no que hoy no se ejecute.
    """
    ofensores = [
        path.relative_to(ROOT)
        for path in (ROOT / "app").rglob("*.py")
        if "import ml" in path.read_text(encoding="utf-8")
        or "from ml" in path.read_text(encoding="utf-8")
    ]
    assert not ofensores, f"app/ importa de ml/: {ofensores}"


def test_la_formula_base_es_la_del_motor() -> None:
    """`potts_guy` aqui debe dar lo mismo que `qspr.ts` en el navegador.

    Si divergen, el backend y el motor darian numeros distintos para la misma
    molecula, que es el fallo que el proyecto entero se organiza para evitar.
    """
    sys.path.insert(0, str(ROOT / "ml"))
    from train_kp import potts_guy  # noqa: PLC0415

    # log Kp = -2.7 + 0.71*logP - 0.0061*MW
    assert potts_guy(138.12, 2.26) == pytest.approx(-2.7 + 0.71 * 2.26 - 0.0061 * 138.12)
    assert potts_guy(0.0, 0.0) == pytest.approx(-2.7)
    # Cafeina: MW 194.19, logP -0.07
    assert potts_guy(194.19, -0.07) == pytest.approx(-3.934, abs=0.01)


def test_el_script_sin_dataset_falla_con_instrucciones() -> None:
    """Sin datos no se entrena, y el mensaje debe decir como conseguirlos."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "ml" / "train_kp.py"), "--dataset", "no/existe.csv"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 1
    assert "build_dataset" in result.stderr


@pytest.mark.skipif(not DATASET.is_file(), reason="dataset no construido todavia")
class TestDataset:
    def test_tiene_las_columnas_necesarias(self) -> None:
        with DATASET.open(encoding="utf-8", newline="") as handle:
            columns = set(next(csv.reader(handle)))
        assert columns >= REQUIRED_COLUMNS

    def test_cada_compuesto_cita_su_procedencia(self) -> None:
        with DATASET.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))

        assert len(rows) > 100, "un conjunto util necesita mas de 100 compuestos"
        for row in rows:
            assert row["source"], f"{row['compound_name']} no declara fuente"
            assert "doi" in row["source"].lower() or row["doi"], (
                f"{row['compound_name']} no es rastreable hasta una publicacion"
            )

    def test_los_compuestos_estan_dentro_del_dominio(self) -> None:
        """Entrenar fuera del dominio declarado invalidaria la comparacion."""
        with DATASET.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))

        for row in rows:
            if row["in_domain"] != "1":
                continue
            assert float(row["molecular_weight"]) <= 500.0
            assert -1.0 <= float(row["log_p"]) <= 6.0


@pytest.mark.skipif(not ARTIFACT.is_file(), reason="modelo no entrenado todavia")
class TestArtefacto:
    def _load(self) -> dict:
        return json.loads(ARTIFACT.read_text(encoding="utf-8"))

    def test_no_se_declara_productivo(self) -> None:
        """Mientras no supere a Potts-Guy, el artefacto no entra al motor."""
        artifact = self._load()
        if artifact["verdict"] != "supera_linea_base_y_ruido":
            assert artifact["in_production"] is False

    def test_declara_el_nivel_del_dato(self) -> None:
        artifact = self._load()
        # Los descriptores salen de RDKit, que los calcula: 'estimated', nunca
        # 'verified'. El modelo hereda el nivel de sus entradas.
        assert artifact["data_level"] == "estimated"
        assert "RDKit" in artifact["caveat"]

    def test_publica_metricas_de_las_dos_partes(self) -> None:
        """Una metrica sin linea base con la que compararla no dice nada."""
        metrics = self._load()["metrics"]
        assert {"mae", "rmse", "r2"} <= set(metrics["ridge_loo"])
        assert {"mae", "rmse", "r2"} <= set(metrics["potts_guy"])

    def test_el_veredicto_es_coherente_con_las_metricas(self) -> None:
        artifact = self._load()
        metrics = artifact["metrics"]
        mejora = metrics["potts_guy"]["mae"] - metrics["ridge_loo"]["mae"]
        suelo = artifact.get("noise_floor_log_units")

        if mejora <= 0:
            assert artifact["verdict"] == "no_supera_la_linea_base"
        elif suelo is not None and mejora <= suelo:
            assert artifact["verdict"] == "mejora_dentro_del_ruido"
        else:
            assert artifact["verdict"] == "supera_linea_base_y_ruido"
