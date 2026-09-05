"""La prueba que ninguna otra puede sustituir: aislamiento real entre usuarios.

Todo lo demas del repositorio verifica que **el servicio se comporta bien**. Esto
verifica que **las politicas de RLS estan bien escritas**, que es una cosa
distinta: una politica mal puesta pasa las 63 pruebas y las 34 comprobaciones de
rutas sin inmutarse, porque ahi PostgreSQL esta sustituido por un doble.

Aqui no hay dobles. Se abren dos sesiones reales contra el Supabase real, el
usuario A crea una simulacion y el usuario B intenta leerla por tres vias
distintas. Si alguna funciona, hay una fuga de datos y da igual todo lo demas.

**Por que 404 y no 403.** Cuando RLS oculta una fila, el servicio no puede
distinguir "no existe" de "es de otro". Devolver 404 es lo correcto: un 403
confirmaria que el recurso existe, que ya es informacion que B no deberia tener.

Requisitos previos (una vez, en el panel de Supabase):
    Authentication → Users → Add user, marcando "Auto Confirm User".
    Crear dos usuarios y pasar sus credenciales a este script.

Uso:
    python scripts/test_rls_isolation.py \\
        --user-a correo-a@dominio.com --pass-a 'clave' \\
        --user-b correo-b@dominio.com --pass-b 'clave'
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import create_app  # noqa: E402

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[1m",
    "\033[0m",
)

# Metricas de ejemplo, en camelCase como las escribe el motor.
METRICS = {
    "logKp": -2.41,
    "permeabilityCmH": 0.0039,
    "maxFluxInfiniteDose": 230.7,
    "lagTimeHours": 1.36,
    "absorbedFractionPct": 96.98,
    "timeTo50PctHours": 5.61,
    "penetrationDepthUm": 310,
    "peakConcentrationVE": 86.2,
    "irritationIndex": 34,
    "irritationBand": "moderate",
    "confidence": "high",
    "outOfDomainReasons": [],
}

INPUT_SNAPSHOT = {
    "ingredient": {
        "name": "Acido salicilico",
        "inciName": "Salicylic Acid",
        "molecularWeight": 138.12,
        "logP": 2.26,
        "pka": 2.97,
        "category": "BHA",
        "riskFlags": ["bha"],
    },
    "vehicle": {"name": "Gel hidroalcoholico", "enhancerFactor": 1.6},
    "concentrationPct": 2.0,
    "pH": 4.0,
    "durationHours": 8.0,
    "appliedDoseMgCm2": 2.0,
}


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool]] = []

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        mark = f"{GREEN}OK{RESET}" if ok else f"{RED}FALLO{RESET}"
        print(f"  {mark:<18} {label}")
        if detail:
            for line in detail.splitlines():
                print(f"    {DIM}{line}{RESET}")
        self.rows.append((label, ok))
        return ok

    def summary(self) -> int:
        passed = sum(1 for _, ok in self.rows if ok)
        total = len(self.rows)
        colour = GREEN if passed == total else RED
        print(f"\n{BOLD}{'=' * 78}{RESET}")
        print(f"{colour}{BOLD}  {passed}/{total} comprobaciones correctas{RESET}")
        if passed != total:
            print(f"\n{RED}  FUGA DE DATOS o configuracion incorrecta:{RESET}")
            for label, ok in self.rows:
                if not ok:
                    print(f"    - {label}")
        print(f"{BOLD}{'=' * 78}{RESET}")
        return 0 if passed == total else 1


def sign_in(base_url: str, api_key: str, email: str, password: str) -> dict[str, Any]:
    response = httpx.post(
        f"{base_url}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": api_key, "Content-Type": "application/json"},
        json={"email": email, "password": password},
        timeout=30.0,
    )
    if response.status_code != 200:
        body = response.json()
        raise SystemExit(
            f"No se pudo iniciar sesion como {email}: "
            f"{body.get('error_code') or body.get('msg') or response.status_code}\n\n"
            "Si dice 'email_not_confirmed', crea el usuario desde el panel con\n"
            "'Auto Confirm User' marcado, o desactiva la confirmacion en\n"
            "Authentication → Providers → Email."
        )
    return response.json()


def rest(
    base_url: str,
    api_key: str,
    token: str,
    method: str,
    path: str,
    **kwargs: Any,
) -> httpx.Response:
    return httpx.request(
        method,
        f"{base_url}/rest/v1{path}",
        headers={
            "apikey": api_key,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            **kwargs.pop("extra_headers", {}),
        },
        timeout=30.0,
        **kwargs,
    )


def _single_user_tail(
    base: str, key: str, token: str, simulation_id: str, report: Report, *, with_ai: bool
) -> None:
    """Recorrido completo del backend contra Supabase real, con un solo usuario.

    Verifica que el servicio funciona de verdad de punta a punta. NO verifica
    aislamiento: para eso hacen falta dos identidades distintas.
    """
    print(f"\n{BOLD}La API del backend contra la base de datos REAL{RESET}")
    app = create_app()
    settings = get_settings()

    with TestClient(app) as client:
        auth = {"Authorization": f"Bearer {token}"}

        response = client.get("/api/v1/ingredients", headers=auth)
        count = response.json().get("count", 0) if response.status_code == 200 else 0
        report.check(
            "GET /ingredients lee el catalogo real",
            response.status_code == 200 and count == 12,
            f"HTTP {response.status_code} · {count} activos",
        )

        response = client.post(
            "/api/v1/descriptors",
            json={"smiles": "CC(=O)Oc1ccccc1C(=O)O"},
            headers=auth,
        )
        body = response.json() if response.status_code == 200 else {}
        report.check(
            "POST /descriptors con token real de Supabase",
            response.status_code == 200,
            f"MW={body.get('molecular_weight')} nivel={body.get('source', {}).get('level')}",
        )

        if with_ai and settings.ai_enabled:
            with client.stream(
                "POST", f"/api/v1/reports/{simulation_id}", json={}, headers=auth
            ) as stream:
                status = stream.status_code
                raw = b"".join(stream.iter_bytes()).decode("utf-8", errors="replace")
            report.check(
                "POST /reports genera por SSE y persiste",
                status == 200 and "event: done" in raw,
                f"HTTP {status} · {len(raw)} bytes de SSE",
            )
            response = httpx.get(
                f"{base}/rest/v1/ai_reports",
                params={
                    "simulation_id": f"eq.{simulation_id}",
                    "select": "id,model,input_tokens,output_tokens",
                },
                headers={"apikey": key, "Authorization": f"Bearer {token}"},
                timeout=30.0,
            )
            rows = response.json() if response.status_code == 200 else []
            report.check(
                "el reporte quedo escrito en ai_reports",
                len(rows) == 1,
                str(rows[0]) if rows else "ninguna fila",
            )

        response = client.get(f"/api/v1/exports/{simulation_id}.xlsx", headers=auth)
        report.check(
            "GET /exports descarga el libro real",
            response.status_code == 200,
            f"HTTP {response.status_code} · {len(response.content) // 1024} KB",
        )


def main() -> int:  # noqa: C901 - recorrido lineal
    parser = argparse.ArgumentParser(description="Prueba de aislamiento RLS entre dos usuarios.")
    parser.add_argument("--user-a", required=True)
    parser.add_argument("--pass-a", required=True)
    # El segundo usuario es opcional: sin el se verifica todo el recorrido real
    # contra Supabase, pero NO el aislamiento, que es justo lo que solo dos
    # identidades distintas pueden demostrar.
    parser.add_argument("--user-b")
    parser.add_argument("--pass-b")
    parser.add_argument("--with-ai", action="store_true", help="Genera un reporte real (cuesta)")
    parser.add_argument("--keep", action="store_true", help="No borrar la simulacion al terminar")
    args = parser.parse_args()

    for handle in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            handle.reconfigure(encoding="utf-8", errors="replace")

    settings = get_settings()
    base = settings.supabase_url
    key = settings.supabase_anon_key
    report = Report()

    print(f"{BOLD}{'=' * 78}")
    print("  DERMASENSE · aislamiento entre usuarios (RLS real, sin dobles)")
    print(f"{'=' * 78}{RESET}")
    print(f"  proyecto: {base}\n")

    # ── Sesiones reales ─────────────────────────────────────────────────────
    print(f"{BOLD}Sesiones{RESET}")
    session_a = sign_in(base, key, args.user_a, args.pass_a)
    token_a = session_a["access_token"]
    uid_a = session_a["user"]["id"]
    report.check("usuario A inicia sesion", True, f"uid {uid_a}")

    token_b = None
    if args.user_b and args.pass_b:
        session_b = sign_in(base, key, args.user_b, args.pass_b)
        token_b = session_b["access_token"]
        uid_b = session_b["user"]["id"]
        report.check("usuario B inicia sesion", True, f"uid {uid_b}")
        report.check("son usuarios distintos", uid_a != uid_b)
    else:
        print(
            f"  {YELLOW}OMITIDO{RESET}           sin segundo usuario: el AISLAMIENTO no se prueba"
        )
        print(f"    {DIM}crealo en Authentication → Users → Add user (Auto Confirm){RESET}")

    # ── El seed llego bien ──────────────────────────────────────────────────
    print(f"\n{BOLD}Catalogos publicos (visibles para cualquier autenticado){RESET}")
    for table, expected in [
        ("ingredients", 12),
        ("vehicles", 6),
        ("skin_models", 6),
        ("validation_records", 229),
    ]:
        response = rest(
            base, key, token_a, "GET", f"/{table}?select=id",
            extra_headers={"Prefer": "count=exact", "Range": "0-0"},
        )
        total = response.headers.get("content-range", "*/0").split("/")[-1]
        count = int(total) if total.isdigit() else 0
        report.check(f"{table}: {count} filas (esperado {expected})", count == expected)

    # ── A crea una simulacion ───────────────────────────────────────────────
    print(f"\n{BOLD}El usuario A crea una simulacion{RESET}")
    created = rest(
        base, key, token_a, "POST", "/simulations",
        extra_headers={"Prefer": "return=representation"},
        json={
            "user_id": uid_a,
            "title": "PRUEBA DE AISLAMIENTO — borrar",
            "concentration_pct": 2.0,
            "ph": 4.0,
            "duration_hours": 8.0,
            "applied_dose_mg_cm2": 2.0,
            "input_snapshot": INPUT_SNAPSHOT,
            "metrics": METRICS,
            "engine_version": "engine-1.0.0",
        },
    )
    if created.status_code >= 400:
        raise SystemExit(
            f"A no pudo crear la simulacion: {created.status_code} {created.text[:300]}"
        )
    simulation_id = created.json()[0]["id"]
    report.check("A crea su simulacion", True, f"id {simulation_id}")

    response = rest(base, key, token_a, "GET", f"/simulations?id=eq.{simulation_id}&select=id")
    report.check("A ve su propia simulacion", len(response.json()) == 1)

    if token_b is None:
        _single_user_tail(base, key, token_a, simulation_id, report, with_ai=args.with_ai)
        if not args.keep:
            rest(base, key, token_a, "DELETE", f"/simulations?id=eq.{simulation_id}")
            print(f"\n{DIM}  simulacion de prueba borrada{RESET}")
        return report.summary()

    # ── B intenta llegar a ella ─────────────────────────────────────────────
    print(f"\n{BOLD}El usuario B intenta acceder a la simulacion de A{RESET}")

    response = rest(base, key, token_b, "GET", f"/simulations?id=eq.{simulation_id}&select=*")
    rows = response.json() if response.status_code == 200 else []
    report.check(
        "PostgREST directo: B no ve la fila",
        response.status_code == 200 and rows == [],
        f"HTTP {response.status_code} · {len(rows)} fila(s)",
    )

    response = rest(
        base, key, token_b, "PATCH", f"/simulations?id=eq.{simulation_id}",
        extra_headers={"Prefer": "return=representation"},
        json={"title": "SECUESTRADA POR B"},
    )
    modified = response.json() if response.status_code == 200 else []
    report.check(
        "PostgREST directo: B no puede modificarla",
        modified == [],
        f"HTTP {response.status_code} · {len(modified)} fila(s) modificada(s)",
    )

    response = rest(
        base, key, token_b, "DELETE", f"/simulations?id=eq.{simulation_id}",
        extra_headers={"Prefer": "return=representation"},
    )
    deleted = response.json() if response.status_code == 200 else []
    report.check(
        "PostgREST directo: B no puede borrarla",
        deleted == [],
        f"HTTP {response.status_code} · {len(deleted)} fila(s) borrada(s)",
    )

    response = rest(base, key, token_a, "GET", f"/simulations?id=eq.{simulation_id}&select=title")
    still_there = response.json()
    report.check(
        "la simulacion de A sigue intacta tras los intentos de B",
        len(still_there) == 1 and still_there[0]["title"] == "PRUEBA DE AISLAMIENTO — borrar",
    )

    # ── A traves del backend ────────────────────────────────────────────────
    print(f"\n{BOLD}A traves de la API del backend{RESET}")
    app = create_app()
    with TestClient(app) as client:
        auth_a = {"Authorization": f"Bearer {token_a}"}
        auth_b = {"Authorization": f"Bearer {token_b}"}

        response = client.get(f"/api/v1/exports/{simulation_id}.xlsx", headers=auth_a)
        report.check(
            "A descarga su Excel",
            response.status_code == 200,
            f"HTTP {response.status_code} · {len(response.content) // 1024} KB",
        )

        response = client.get(f"/api/v1/exports/{simulation_id}.xlsx", headers=auth_b)
        code = response.json().get("error", {}).get("code") if response.status_code != 200 else None
        report.check(
            "B recibe 404 al pedir el Excel de A",
            response.status_code == 404,
            f"HTTP {response.status_code} · {code}"
            + ("\nEL ARCHIVO SE ENTREGO: hay fuga de datos" if response.status_code == 200 else ""),
        )

        response = client.post(f"/api/v1/reports/{simulation_id}", json={}, headers=auth_b)
        report.check(
            "B recibe 404 al pedir el reporte de A",
            response.status_code == 404,
            f"HTTP {response.status_code}",
        )

        response = client.get("/api/v1/ingredients", headers=auth_b)
        count = response.json().get("count", 0) if response.status_code == 200 else 0
        report.check(
            "B si ve el catalogo publico (no todo esta cerrado)",
            response.status_code == 200 and count == 12,
            f"HTTP {response.status_code} · {count} activos",
        )

    # ── Limpieza ────────────────────────────────────────────────────────────
    if not args.keep:
        rest(base, key, token_a, "DELETE", f"/simulations?id=eq.{simulation_id}")
        print(f"\n{DIM}  simulacion de prueba borrada{RESET}")
    else:
        print(f"\n{YELLOW}  simulacion conservada: {simulation_id}{RESET}")

    return report.summary()


if __name__ == "__main__":
    raise SystemExit(main())
