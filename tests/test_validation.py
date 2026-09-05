"""La capa de evidencia: que no prometa mas precision de la que tiene.

Estas pruebas no comprueban que el modelo acierte —no acierta especialmente
bien, y eso ya esta medido en README §5—. Comprueban que **el error se reporte
con honestidad**: que la formula sea la del motor, que el dominio se evalue
sobre el logP que llega, y sobre todo que no se invente un error local cuando
los compuestos "parecidos" no se parecen en nada.
"""

from __future__ import annotations

import pytest

from app.services import validation as service
from app.services.validation import MeasuredCompound

# Conjunto minimo y controlado: dos grupos separados en el espacio de
# descriptores, para poder razonar sobre que es "vecino" y que no.
COMPOUNDS = [
    # Grupo A: MW ~130-160, logP ~2
    MeasuredCompound("Alfa", 130.0, 2.0, -2.0, "test"),
    MeasuredCompound("Beta", 140.0, 2.1, -2.1, "test"),
    MeasuredCompound("Gamma", 150.0, 1.9, -1.8, "test"),
    MeasuredCompound("Delta", 160.0, 2.2, -2.3, "test"),
    # Grupo B: MW ~400, logP ~5
    MeasuredCompound("Epsilon", 400.0, 5.0, -3.0, "test"),
    MeasuredCompound("Zeta", 410.0, 5.1, -3.2, "test"),
]


def test_la_formula_es_la_del_motor() -> None:
    """Si diverge de packages/engine/qspr.ts, el backend y el motor mienten distinto."""
    assert service.potts_guy(138.12, 2.26) == pytest.approx(
        -2.7 + 0.71 * 2.26 - 0.0061 * 138.12
    )
    assert service.potts_guy(0.0, 0.0) == pytest.approx(-2.7)


def test_el_dominio_usa_el_logp_que_recibe() -> None:
    """El caso de la cafeina, que es real y decide el veredicto por una decima.

    RDKit (Crippen) calcula logP -1.03 y PubChem publica -0.07 para la misma
    molecula. Con el primero queda fuera del dominio; con el segundo, dentro.
    La funcion no debe elegir por su cuenta: usa el que le pasan.
    """
    fuera, razones = service.check_domain(194.19, -1.03)
    assert fuera is False
    assert "hidrofilico" in razones[0]

    dentro, sin_razones = service.check_domain(194.19, -0.07)
    assert dentro is True
    assert sin_razones == []


def test_peso_molecular_excesivo_sale_del_dominio() -> None:
    ok, razones = service.check_domain(5000.0, -4.5)
    assert ok is False
    # Dos motivos: MW y logP. Ambos deben declararse, no solo el primero.
    assert len(razones) == 2


def test_el_error_local_usa_solo_a_los_vecinos() -> None:
    """El error local debe diferir del global cuando la vecindad es distinta."""
    resultado = service.evaluate(COMPOUNDS, molecular_weight=140.0, log_p=2.0, neighbours=3)
    evidencia = resultado["evidence"]

    assert evidencia["n_neighbours"] == 3
    assert evidencia["neighbours_representative"] is True
    # Los tres vecinos son del grupo A; ninguno del grupo B.
    nombres = {n["compound_name"] for n in evidencia["neighbours"]}
    assert nombres <= {"Alfa", "Beta", "Gamma", "Delta"}


def test_no_inventa_error_local_si_los_vecinos_estan_lejos() -> None:
    """El caso del acido hialuronico: MW 5000 no se parece a nada del conjunto.

    Siempre existe un compuesto "menos lejano" que los demas, pero eso no lo
    hace comparable. Dar un error local a partir de ahi seria falsa precision.
    """
    resultado = service.evaluate(COMPOUNDS, molecular_weight=5000.0, log_p=-4.5)
    evidencia = resultado["evidence"]

    assert evidencia["neighbours_representative"] is False
    assert evidencia["local_mae"] is None
    assert evidencia["local_rmse"] is None
    assert evidencia["max_neighbour_distance"] > service.MAX_NEIGHBOUR_DISTANCE


def test_el_error_global_siempre_se_publica() -> None:
    """Aunque la vecindad no sirva, el error global del modelo si es un dato."""
    resultado = service.evaluate(COMPOUNDS, molecular_weight=5000.0, log_p=-4.5)
    assert resultado["evidence"]["global_mae"] > 0
    assert resultado["evidence"]["n_measured"] == len(COMPOUNDS)


def test_coincidencia_exacta_por_nombre() -> None:
    resultado = service.evaluate(
        COMPOUNDS, molecular_weight=130.0, log_p=2.0, name="alfa"
    )
    exacta = resultado["evidence"]["exact_match"]

    assert exacta is not None
    assert exacta["compound_name"] == "Alfa"
    assert exacta["log_kp_measured"] == -2.0
    assert exacta["absolute_error"] == pytest.approx(
        abs(service.potts_guy(130.0, 2.0) - (-2.0)), abs=0.001
    )


def test_sin_coincidencia_exacta_no_se_fuerza_ninguna() -> None:
    resultado = service.evaluate(
        COMPOUNDS, molecular_weight=200.0, log_p=1.0, name="Compuesto inexistente"
    )
    assert resultado["evidence"]["exact_match"] is None


def test_un_conjunto_vacio_no_revienta() -> None:
    resultado = service.evaluate([], molecular_weight=138.0, log_p=2.0)
    evidencia = resultado["evidence"]

    assert evidencia["n_measured"] == 0
    assert evidencia["neighbours"] == []
    assert evidencia["neighbours_representative"] is False
    assert resultado["prediction"]["log_kp"] == pytest.approx(
        service.potts_guy(138.0, 2.0), abs=0.01
    )


def test_las_filas_corruptas_se_ignoran_sin_perder_el_resto() -> None:
    filas = [
        {"compound_name": "Bueno", "molecular_weight": "138.1", "log_p": "2.2",
         "log_kp_measured": "-2.4", "source": "x"},
        {"compound_name": "Malo", "molecular_weight": "no-es-un-numero", "log_p": "2.2",
         "log_kp_measured": "-2.4", "source": "x"},
        {"compound_name": "Incompleto"},
    ]
    compuestos = service.parse_rows(filas)
    assert [c.name for c in compuestos] == ["Bueno"]


# ── El endpoint ─────────────────────────────────────────────────────────────

def test_endpoint_devuelve_prediccion_y_evidencia(client) -> None:
    response = client.get(
        "/api/v1/validation",
        params={"molecular_weight": 138.12, "log_p": 2.26, "name": "Acido salicilico"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["prediction"]["model"] == "Potts & Guy (1992)"
    assert body["domain"]["in_domain"] is True
    assert body["evidence"]["n_measured"] > 100
    assert body["disclaimer"]


def test_endpoint_NO_exige_sesion(client) -> None:
    """Evidencia publicada: pedir login solo la escondería.

    No gasta tokens, no toca datos de usuario y no dice nada que no este ya en
    una revista. El unico endpoint que si exige identidad es el de IA, por el
    coste. Si esta prueba empieza a fallar con 401, la pantalla de formulacion
    se queda vacia para quien no ha entrado.
    """
    response = client.get("/api/v1/validation", params={"molecular_weight": 138, "log_p": 2})
    assert response.status_code == 200


def test_regulatorio_tampoco_exige_sesion(client) -> None:
    response = client.post(
        "/api/v1/regulatory/check",
        json={"ingredient_name": "Acido salicilico", "concentration_pct": 5.0,
              "product_type": "leave_on", "jurisdictions": ["eu"]},
    )
    assert response.status_code == 200
    assert response.json()["summary"] == "fail"


def test_endpoint_valida_los_parametros(client) -> None:
    response = client.get("/api/v1/validation", params={"molecular_weight": -5, "log_p": 2})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_el_conjunto_servido_trae_los_compuestos_esperados() -> None:
    """El artefacto que sirve la API debe existir en el despliegue."""
    compuestos = service.load_reference_set()
    assert len(compuestos) > 200
    assert all(c.name for c in compuestos)
