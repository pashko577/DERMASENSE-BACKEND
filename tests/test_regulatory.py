"""Reglas regulatorias: coincidencia, limites y —sobre todo— cobertura.

La prueba mas importante de este archivo no es que detecte un exceso de
concentracion, sino que **no confunda ausencia de regla con aprobacion**. Un
sistema que responde "todo correcto" ante un activo que no conoce es peor que no
tener verificacion.
"""

from __future__ import annotations

from datetime import date

from app.schemas.report import RegulatoryCheckRequest
from app.services.regulatory_rules import check, load_ruleset, normalize


def test_normaliza_acentos_y_mayusculas() -> None:
    assert normalize("Ácido Salicílico") == "acido salicilico"
    assert normalize("ALFA-ARBUTINA") == "alfa arbutina"


def test_sustancia_prohibida_falla() -> None:
    resultado = check(
        RegulatoryCheckRequest(
            ingredient_name="Hidroquinona",
            concentration_pct=0.5,
            jurisdictions=["eu"],
        )
    )

    prohibicion = [f for f in resultado.findings if "Anexo II" in f.requirement]
    assert prohibicion and prohibicion[0].outcome == "fail"
    assert resultado.summary == "fail"


def test_concentracion_sobre_el_limite_falla() -> None:
    resultado = check(
        RegulatoryCheckRequest(
            ingredient_name="Acido salicilico",
            concentration_pct=5.0,
            product_type="leave_on",
            jurisdictions=["eu"],
        )
    )

    limite = [f for f in resultado.findings if f.limit_pct is not None]
    assert limite and limite[0].outcome == "fail"
    assert limite[0].limit_pct == 2.0
    assert resultado.summary == "fail"


def test_concentracion_holgada_pasa() -> None:
    resultado = check(
        RegulatoryCheckRequest(
            ingredient_name="Salicylic Acid",
            concentration_pct=0.5,
            product_type="leave_on",
            jurisdictions=["eu"],
        )
    )

    limite = [f for f in resultado.findings if f.limit_pct is not None]
    assert limite and limite[0].outcome == "pass"


def test_concentracion_al_borde_pide_atencion() -> None:
    # 1.9 % sobre un maximo de 2 % cumple, pero no deja margen de proceso.
    resultado = check(
        RegulatoryCheckRequest(
            ingredient_name="Acido salicilico",
            concentration_pct=1.9,
            product_type="leave_on",
            jurisdictions=["eu"],
        )
    )

    limite = [f for f in resultado.findings if f.limit_pct is not None]
    assert limite and limite[0].outcome == "attention"


def test_el_limite_depende_del_tipo_de_producto() -> None:
    champu = check(
        RegulatoryCheckRequest(
            ingredient_name="Acido salicilico",
            concentration_pct=2.5,
            product_type="rinse_off_hair",
            jurisdictions=["eu"],
        )
    )
    crema = check(
        RegulatoryCheckRequest(
            ingredient_name="Acido salicilico",
            concentration_pct=2.5,
            product_type="leave_on",
            jurisdictions=["eu"],
        )
    )

    limite_champu = next(f for f in champu.findings if f.limit_pct is not None)
    limite_crema = next(f for f in crema.findings if f.limit_pct is not None)

    assert limite_champu.limit_pct == 3.0
    assert limite_champu.outcome != "fail"
    assert limite_crema.limit_pct == 2.0
    assert limite_crema.outcome == "fail"


def test_activo_desconocido_no_se_aprueba() -> None:
    resultado = check(
        RegulatoryCheckRequest(
            ingredient_name="Extracto propietario XR-9",
            concentration_pct=1.0,
            jurisdictions=["eu"],
        )
    )

    cobertura = [f for f in resultado.findings if f.requirement.startswith("Cobertura")]
    assert cobertura and cobertura[0].outcome == "unknown"
    assert "NO significa que su uso este permitido" in cobertura[0].message
    assert resultado.summary != "pass"


def test_coincide_por_nombre_inci() -> None:
    resultado = check(
        RegulatoryCheckRequest(
            ingredient_name="Nombre comercial cualquiera",
            inci_name="Retinol",
            concentration_pct=0.1,
            product_type="body_lotion",
            jurisdictions=["eu"],
        )
    )

    limite = next(f for f in resultado.findings if f.limit_pct is not None)
    assert limite.limit_pct == 0.05
    assert limite.outcome == "fail"


def test_mocra_impone_obligaciones_sin_limites() -> None:
    resultado = check(
        RegulatoryCheckRequest(
            ingredient_name="Niacinamida",
            concentration_pct=5.0,
            jurisdictions=["us"],
        )
    )

    obligaciones = [f for f in resultado.findings if f.outcome == "attention"]
    requisitos = " ".join(f.requirement for f in obligaciones)
    assert "substanciacion" in requisitos.lower() or "Substanciacion" in requisitos
    assert all(f.jurisdiction == "us" for f in resultado.findings)


def test_cada_hallazgo_cita_su_fuente_y_su_fecha() -> None:
    resultado = check(
        RegulatoryCheckRequest(
            ingredient_name="Acido salicilico",
            concentration_pct=1.0,
            jurisdictions=["eu", "us"],
        )
    )

    assert resultado.findings
    for finding in resultado.findings:
        assert finding.source
        assert finding.checked_at and finding.checked_at != "None"


def test_entrada_caducada_se_advierte() -> None:
    # Diez anos despues, ninguna entrada puede seguir afirmandose sin revisar.
    resultado = check(
        RegulatoryCheckRequest(
            ingredient_name="Acido salicilico",
            concentration_pct=1.0,
            product_type="leave_on",
            jurisdictions=["eu"],
        ),
        today=date(2036, 1, 1),
    )

    limite = next(f for f in resultado.findings if f.limit_pct is not None)
    assert "hace mas de" in limite.message


def test_los_dos_conjuntos_cargan_y_declaran_su_descargo() -> None:
    for jurisdiccion in ("eu", "us"):
        documento = load_ruleset(jurisdiccion)
        assert documento["jurisdiction"] == jurisdiccion
        assert documento["source"].startswith("http")
        assert documento["disclaimer"]
        for regla in documento.get("rules", []):
            assert regla["requirement"] and regla["message"]
            assert regla.get("source") or documento["source"]
