"""Evaluador de las reglas regulatorias en YAML.

El README principal §18 fija la naturaleza de esta capa: *"basada en reglas y
fuentes, no una prediccion"*. Este modulo es, por tanto, deliberadamente tonto:
normaliza un nombre, busca coincidencias y compara numeros. Toda la inteligencia
esta en los archivos YAML, que un formulador puede auditar sin leer Python.

Lo que este modulo no hace, y no debe hacer nunca: inferir un limite que no este
escrito, extrapolar de una sustancia a otra parecida, o convertir la ausencia de
regla en una aprobacion.
"""

from __future__ import annotations

import unicodedata
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.schemas.report import (
    Jurisdiction,
    RegulatoryCheckRequest,
    RegulatoryCheckResponse,
    RegulatoryFinding,
    RuleOutcome,
)

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"

_FILES: dict[str, str] = {"eu": "eu_1223_2009.yaml", "us": "us_mocra.yaml"}

# Una entrada verificada hace mas de un ano deja de ser una afirmacion util
# sobre el derecho vigente: los anexos se modifican varias veces al ano.
_STALE_AFTER_DAYS = 365

_SEVERITY: dict[str, int] = {
    "pass": 0,
    "not_applicable": 0,
    "unknown": 1,
    "attention": 2,
    "fail": 3,
}

DISCLAIMER = (
    "Resultado preliminar generado por una capa de reglas con fuentes citadas. "
    "No es una validacion regulatoria ni sustituye la evaluacion de seguridad de "
    "una persona cualificada."
)


def normalize(value: str) -> str:
    """Minusculas sin acentos ni signos, para comparar nombres comerciales.

    'Ácido Salicílico', 'acido salicilico' y 'ACIDO-SALICILICO' deben coincidir;
    el catalogo se cura a mano y la escritura varia entre proveedores.
    """
    decomposed = unicodedata.normalize("NFD", value.strip().lower())
    stripped = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join(stripped.replace("-", " ").replace("_", " ").split())


@lru_cache(maxsize=8)
def load_ruleset(jurisdiction: str) -> dict[str, Any]:
    filename = _FILES.get(jurisdiction)
    if filename is None:
        raise ValueError(f"Jurisdiccion sin archivo de reglas: {jurisdiction}")
    path = RULES_DIR / filename
    with path.open("r", encoding="utf-8") as handle:
        document: dict[str, Any] = yaml.safe_load(handle)
    return document


def _matches(rule: dict[str, Any], names: list[str], cas: str | None) -> bool:
    identifiers = rule.get("identifiers") or {}
    rule_names = {normalize(str(name)) for name in identifiers.get("names", [])}
    if rule_names & set(names):
        return True
    return bool(cas) and cas.strip() in {
        str(item).strip() for item in identifiers.get("cas", [])
    }


def _applicable_limit(rule: dict[str, Any], product_type: str) -> dict[str, Any] | None:
    """Limite mas restrictivo entre los que cubren este tipo de producto."""
    candidates = [
        limit
        for limit in rule.get("limits", [])
        if product_type in {str(item) for item in limit.get("product_types", [])}
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda limit: float(limit.get("max_pct", float("inf"))))


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _staleness_note(checked_at: Any, today: date) -> str:
    parsed = _parse_date(checked_at)
    if parsed is None:
        return " La fecha de verificacion de esta entrada no es legible."
    if (today - parsed).days > _STALE_AFTER_DAYS:
        return (
            f" Advertencia: esta entrada se verifico el {parsed.isoformat()}, hace mas de "
            "un ano. Contrastala contra el texto vigente antes de usarla."
        )
    return ""


def _evaluate_rule(
    rule: dict[str, Any],
    document: dict[str, Any],
    request: RegulatoryCheckRequest,
    today: date,
) -> RegulatoryFinding:
    jurisdiction: Jurisdiction = document["jurisdiction"]
    checked_at = rule.get("checked_at") or document.get("checked_at")
    note = _staleness_note(checked_at, today)
    conditions = rule.get("conditions") or []
    condition_text = ("  Condiciones: " + " ".join(conditions)) if conditions else ""

    if rule.get("type") == "prohibited":
        return RegulatoryFinding(
            jurisdiction=jurisdiction,
            regulation=document["regulation"],
            requirement=rule["requirement"],
            outcome="fail",
            message=f"{rule['message'].strip()}{condition_text}{note}",
            source=rule.get("source") or document["source"],
            checked_at=str(checked_at),
            limit_pct=None,
        )

    limit = _applicable_limit(rule, request.product_type)
    if limit is None:
        return RegulatoryFinding(
            jurisdiction=jurisdiction,
            regulation=document["regulation"],
            requirement=rule["requirement"],
            outcome="unknown",
            message=(
                f"La sustancia esta regulada, pero el archivo de reglas no cubre el tipo de "
                f"producto '{request.product_type}'. Revisa el texto original."
                f"{condition_text}{note}"
            ),
            source=rule.get("source") or document["source"],
            checked_at=str(checked_at),
            limit_pct=None,
        )

    max_pct = float(limit["max_pct"])
    ratio_threshold = float(document.get("proximity_ratio", 0.8))

    if request.concentration_pct > max_pct:
        outcome: RuleOutcome = "fail"
        headline = (
            f"La concentracion de {request.concentration_pct} % excede el maximo de "
            f"{max_pct} % para '{request.product_type}'."
        )
    elif request.concentration_pct >= max_pct * ratio_threshold:
        outcome = "attention"
        headline = (
            f"La concentracion de {request.concentration_pct} % esta dentro del maximo de "
            f"{max_pct} %, pero deja poco margen ante la variabilidad del proceso."
        )
    else:
        outcome = "pass"
        headline = (
            f"La concentracion de {request.concentration_pct} % esta por debajo del maximo "
            f"de {max_pct} % para '{request.product_type}'."
        )

    return RegulatoryFinding(
        jurisdiction=jurisdiction,
        regulation=document["regulation"],
        requirement=rule["requirement"],
        outcome=outcome,
        message=f"{headline} {rule['message'].strip()}{condition_text}{note}",
        source=rule.get("source") or document["source"],
        checked_at=str(checked_at),
        limit_pct=max_pct,
    )


def check(request: RegulatoryCheckRequest, *, today: date | None = None) -> RegulatoryCheckResponse:
    today = today or date.today()
    names = [normalize(request.ingredient_name)]
    if request.inci_name:
        names.append(normalize(request.inci_name))

    findings: list[RegulatoryFinding] = []

    for jurisdiction in request.jurisdictions:
        document = load_ruleset(jurisdiction)
        matched = False

        for rule in document.get("rules", []):
            if _matches(rule, names, request.cas_number):
                matched = True
                findings.append(_evaluate_rule(rule, document, request, today))

        if not matched:
            # La ausencia de regla no es una aprobacion. Decirlo explicitamente
            # es la diferencia entre una herramienta util y una que engana.
            findings.append(
                RegulatoryFinding(
                    jurisdiction=document["jurisdiction"],
                    regulation=document["regulation"],
                    requirement="Cobertura del conjunto de reglas",
                    outcome="unknown",
                    message=(
                        f"'{request.ingredient_name}' no aparece en el conjunto de reglas "
                        "cargado. Esto NO significa que su uso este permitido: significa que "
                        "esta verificacion no dice nada al respecto."
                    ),
                    source=document["source"],
                    checked_at=str(document.get("checked_at")),
                    limit_pct=None,
                )
            )

        for obligation in document.get("obligations", []):
            findings.append(
                RegulatoryFinding(
                    jurisdiction=document["jurisdiction"],
                    regulation=document["regulation"],
                    requirement=obligation["requirement"],
                    outcome="attention",
                    message=obligation["message"].strip(),
                    source=obligation.get("source") or document["source"],
                    checked_at=str(obligation.get("checked_at") or document.get("checked_at")),
                    limit_pct=None,
                )
            )

    summary: RuleOutcome = "pass"
    if findings:
        summary = max((f.outcome for f in findings), key=lambda outcome: _SEVERITY[outcome])

    return RegulatoryCheckResponse(
        ingredient_name=request.ingredient_name,
        concentration_pct=request.concentration_pct,
        product_type=request.product_type,
        findings=findings,
        summary=summary,
        disclaimer=DISCLAIMER,
    )
