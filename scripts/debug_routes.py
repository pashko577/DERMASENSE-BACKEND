"""Recorre TODAS las rutas de la API y reporta que hace cada una.

Que verifica de verdad y que no:

- **Real:** RDKit, PubChem (sale a internet), el motor de reglas, openpyxl, la
  verificacion de JWT, el formato de error, y —si hay clave— el proveedor de IA
  con una respuesta de verdad.
- **Sustituido:** PostgreSQL. Se usa un doble en memoria porque todavia no hay
  proyecto Supabase. Es la unica pieza simulada, y esta marcada como tal en la
  salida para que nadie confunda "la ruta responde" con "RLS funciona".

Se ejecuta dentro del proceso, sin levantar servidor: asi la salida es
determinista y no depende de puertos ni de esperar arranques.

Uso:
    python scripts/debug_routes.py
    python scripts/debug_routes.py --no-ai      # omite la llamada que cuesta dinero
    python scripts/debug_routes.py --no-network # omite tambien PubChem
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from jose import jwt  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.deps import get_db  # noqa: E402
from app.main import create_app  # noqa: E402

USER_ID = "11111111-1111-4111-8111-111111111111"
OTHER_USER_ID = "22222222-2222-4222-8222-222222222222"
SIMULATION_ID = "33333333-3333-4333-8333-333333333333"

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[1m",
    "\033[0m",
)


# ── Doble de PostgREST ──────────────────────────────────────────────────────


class MemoryDb:
    """Sustituto en memoria del cliente PostgREST.

    Interpreta lo justo del lenguaje de filtros de PostgREST (`eq.`) para que
    las rutas se comporten como en produccion. No implementa RLS: RLS vive en
    Postgres y no puede simularse aqui, que es justo el motivo de que este
    script no sustituya a la prueba contra un Supabase real.
    """

    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {
            "simulations": [simulation_row()],
            "ai_reports": [],
            "ingredients": ingredient_rows(),
        }
        self.log: list[str] = []

    @staticmethod
    def _matches(row: dict[str, Any], filters: dict[str, Any] | None) -> bool:
        for column, expression in (filters or {}).items():
            text = str(expression)
            if text.startswith("eq."):
                if str(row.get(column)) != text[3:]:
                    return False
            elif text.startswith("gte.") or text.startswith("ilike."):
                continue  # no afecta a los casos que recorre este script
        return True

    async def select(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: dict[str, Any] | None = None,
        order: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        self.log.append(f"select {table} {filters or ''}")
        rows = [row for row in self.tables.get(table, []) if self._matches(row, filters)]
        return rows[:limit] if limit is not None else rows

    async def select_one(
        self, table: str, *, columns: str = "*", filters: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        rows = await self.select(table, columns=columns, filters=filters, limit=1)
        return rows[0] if rows else None

    async def insert(
        self, table: str, row: dict[str, Any], *, returning: bool = True
    ) -> dict[str, Any] | None:
        self.log.append(f"insert {table}")
        stored = {**row, "id": "44444444-4444-4444-8444-444444444444"}
        self.tables.setdefault(table, []).append(stored)
        return stored

    async def delete(self, table: str, *, filters: dict[str, Any]) -> int:
        self.log.append(f"delete {table} {filters}")
        before = len(self.tables.get(table, []))
        self.tables[table] = [
            row for row in self.tables.get(table, []) if not self._matches(row, filters)
        ]
        return before - len(self.tables[table])

    async def count(self, table: str, *, filters: dict[str, Any] | None = None) -> int:
        return len(self.tables.get(table, []))


def simulation_row(user_id: str = USER_ID) -> dict[str, Any]:
    return {
        "id": SIMULATION_ID,
        "user_id": user_id,
        "title": "Salicilico 2% en gel hidroalcoholico",
        "ingredient_id": "55555555-5555-4555-8555-555555555555",
        "vehicle_id": "66666666-6666-4666-8666-666666666666",
        "skin_model_id": None,
        "concentration_pct": 2.0,
        "ph": 4.0,
        "duration_hours": 8.0,
        "applied_dose_mg_cm2": 2.0,
        "engine_version": "engine-1.0.0",
        "notes": None,
        "created_at": "2026-09-05T10:00:00+00:00",
        "input_snapshot": {
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
        },
        "metrics": {
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
        },
    }


def ingredient_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": "55555555-5555-4555-8555-555555555555",
            "owner_id": None,
            "name": "Acido salicilico",
            "inci_name": "Salicylic Acid",
            "molecular_weight": 138.12,
            "log_p": 2.26,
            "pka": 2.97,
            "category": "BHA",
            "risk_flags": ["bha"],
            "sources": {
                "log_p": {
                    "db": "PubChem",
                    "id": "CID 338",
                    "type": "experimental",
                    "level": "verified",
                }
            },
            "data_level": "verified",
            "max_use_concentration": 2.0,
            "regulation_ref": "Reg. (CE) 1223/2009 Anexo III",
            "regulation_version": "consolidado",
            "regulation_checked_at": "2026-09-05",
        }
    ]


def make_token(user_id: str = USER_ID, *, expires_in: int = 3600) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "aud": settings.supabase_jwt_audience,
            "role": "authenticated",
            "email": "formulador@ejemplo.test",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        },
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )


# ── Motor de comprobaciones ─────────────────────────────────────────────────


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, bool, str]] = []

    def check(
        self,
        route: str,
        case: str,
        expected: int,
        got: int,
        detail: str = "",
        elapsed: float | None = None,
    ) -> bool:
        ok = expected == got
        timing = f" {elapsed * 1000:.0f}ms" if elapsed is not None else ""
        mark = f"{GREEN}OK{RESET}" if ok else f"{RED}FALLO{RESET}"
        print(
            f"  {mark:<18} {case:<44} esperado {expected} · recibido {got}{DIM}{timing}{RESET}"
        )
        if detail:
            for line in detail.splitlines():
                print(f"    {DIM}{line}{RESET}")
        self.rows.append((route, case, f"{expected}->{got}", ok, detail[:120]))
        return ok

    def note(self, text: str) -> None:
        print(f"  {YELLOW}NOTA{RESET}              {text}")

    def summary(self) -> int:
        passed = sum(1 for row in self.rows if row[3])
        total = len(self.rows)
        colour = GREEN if passed == total else RED
        print(f"\n{BOLD}{'=' * 78}{RESET}")
        print(f"{colour}{BOLD}  {passed}/{total} comprobaciones correctas{RESET}")
        if passed != total:
            print(f"\n{RED}  Fallos:{RESET}")
            for route, case, transition, ok, _ in self.rows:
                if not ok:
                    print(f"    - {route} · {case} ({transition})")
        print(f"{BOLD}{'=' * 78}{RESET}")
        return 0 if passed == total else 1


def section(title: str) -> None:
    print(f"\n{BOLD}{title}{RESET}")


def main() -> int:  # noqa: C901 - un recorrido lineal se lee mejor entero
    parser = argparse.ArgumentParser(description="Depura todas las rutas de la API.")
    parser.add_argument("--no-ai", action="store_true", help="Omite la llamada al proveedor de IA")
    parser.add_argument("--no-network", action="store_true", help="Omite tambien PubChem")
    args = parser.parse_args()

    # La consola de Windows usa cp1252 por defecto y revienta con los
    # caracteres de caja y las tildes. Se fuerza UTF-8 antes de imprimir nada.
    for handle in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            handle.reconfigure(encoding="utf-8", errors="replace")

    settings = get_settings()
    if not settings.supabase_jwt_secret:
        print(
            "SUPABASE_JWT_SECRET no esta configurado: este script firma tokens "
            "simetricos y no puede continuar sin el.",
            file=sys.stderr,
        )
        return 1

    db = MemoryDb()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db

    report = Report()
    token = make_token()
    auth = {"Authorization": f"Bearer {token}"}

    print(f"{BOLD}{'=' * 78}")
    print("  DERMASENSE-BACKEND · depuracion de rutas")
    print(f"{'=' * 78}{RESET}")
    print(f"  proveedor de IA : {settings.ai_provider} · {settings.ai_model}")
    print(f"  IA configurada  : {settings.ai_enabled}")
    print(
        f"  {YELLOW}PostgreSQL      : SUSTITUIDO por un doble en memoria "
        f"(RLS no se prueba){RESET}"
    )

    with TestClient(app) as client:
        # ── /health ─────────────────────────────────────────────────────────
        section("GET /health")
        response = client.get("/health")
        body = response.json()
        report.check(
            "/health",
            "sonda sin autenticacion",
            200,
            response.status_code,
            json.dumps(body.get("capabilities", {}), ensure_ascii=False),
        )

        # ── Autenticacion, transversal ──────────────────────────────────────
        section("Autenticacion (aplica a todas las rutas de /api/v1)")
        report.check(
            "auth",
            "sin encabezado Authorization",
            401,
            client.post("/api/v1/descriptors", json={"smiles": "CCO"}).status_code,
        )
        report.check(
            "auth",
            "esquema no Bearer",
            401,
            client.post(
                "/api/v1/descriptors",
                json={"smiles": "CCO"},
                headers={"Authorization": "Basic dXN1YXJpbzpjbGF2ZQ=="},
            ).status_code,
        )
        report.check(
            "auth",
            "token que no es un JWT",
            401,
            client.post(
                "/api/v1/descriptors",
                json={"smiles": "CCO"},
                headers={"Authorization": "Bearer no.es.jwt"},
            ).status_code,
        )
        report.check(
            "auth",
            "token expirado",
            401,
            client.post(
                "/api/v1/descriptors",
                json={"smiles": "CCO"},
                headers={"Authorization": f"Bearer {make_token(expires_in=-60)}"},
            ).status_code,
        )
        response = client.post("/api/v1/descriptors", json={"smiles": "CCO"})
        shape = set(response.json().get("error", {}).keys())
        report.check(
            "auth",
            "formato de error del contrato",
            200 if shape == {"code", "message", "details"} else 0,
            200,
            f"claves: {sorted(shape)}",
        )

        # ── /descriptors ────────────────────────────────────────────────────
        section("POST /api/v1/descriptors  (RDKit real)")
        started = time.perf_counter()
        response = client.post(
            "/api/v1/descriptors",
            json={"smiles": "CC(=O)Oc1ccccc1C(=O)O"},
            headers=auth,
        )
        elapsed = time.perf_counter() - started
        body = response.json() if response.status_code == 200 else {}
        detail = ""
        if body:
            detail = (
                f"MW={body['molecular_weight']} logP={body['log_p']} TPSA={body['tpsa']} "
                f"formula={body['formula']} nivel={body['source']['level']}"
            )
        report.check(
            "/descriptors", "aspirina (CID 2244)", 200, response.status_code, detail, elapsed
        )

        if body:
            report.check(
                "/descriptors",
                "MW coincide con PubChem (180.16)",
                200 if abs(body["molecular_weight"] - 180.16) < 0.05 else 0,
                200,
            )
            report.check(
                "/descriptors",
                "logP calculado NUNCA es 'verified'",
                200 if body["source"]["level"] == "estimated" else 0,
                200,
            )

        report.check(
            "/descriptors",
            "SMILES sin sentido",
            400,
            client.post("/api/v1/descriptors", json={"smiles": "%%%"}, headers=auth).status_code,
        )
        report.check(
            "/descriptors",
            "payload sin el campo smiles",
            400,
            client.post("/api/v1/descriptors", json={}, headers=auth).status_code,
        )

        # ── /ingredients ────────────────────────────────────────────────────
        section("GET /api/v1/ingredients  (base sustituida)")
        response = client.get("/api/v1/ingredients", headers=auth)
        body = response.json() if response.status_code == 200 else {}
        report.check(
            "/ingredients",
            "catalogo",
            200,
            response.status_code,
            f"{body.get('count', 0)} activo(s); procedencia por campo presente",
        )

        # ── /ingredients/resolve ────────────────────────────────────────────
        section("POST /api/v1/ingredients/resolve  (PubChem real)")
        if args.no_network or args.no_ai:
            report.note("omitido por --no-network/--no-ai")
        else:
            started = time.perf_counter()
            response = client.post(
                "/api/v1/ingredients/resolve",
                json={"name": "caffeine", "limit": 2},
                headers=auth,
            )
            elapsed = time.perf_counter() - started
            body = response.json() if response.status_code == 200 else {}
            detail = ""
            if body.get("candidates"):
                first = body["candidates"][0]
                detail = (
                    f"CID {first['cid']} · MW {first['molecular_weight']} "
                    f"· XLogP {first['xlogp']}"
                )
                if first.get("warnings"):
                    detail += f"\naviso: {first['warnings'][0][:100]}"
            report.check(
                "/ingredients/resolve", "cafeina", 200, response.status_code, detail, elapsed
            )

        # ── /regulatory/check ───────────────────────────────────────────────
        section("POST /api/v1/regulatory/check  (reglas YAML reales)")
        for label, payload, expected_summary in [
            (
                "salicilico 5% leave-on (excede el 2%)",
                {
                    "ingredient_name": "Acido salicilico",
                    "concentration_pct": 5.0,
                    "product_type": "leave_on",
                    "jurisdictions": ["eu"],
                },
                "fail",
            ),
            (
                "salicilico 0.5% leave-on (holgado)",
                {
                    "ingredient_name": "Acido salicilico",
                    "concentration_pct": 0.5,
                    "product_type": "leave_on",
                    "jurisdictions": ["eu"],
                },
                "attention",
            ),
            (
                "hidroquinona (prohibida, Anexo II)",
                {
                    "ingredient_name": "Hidroquinona",
                    "concentration_pct": 0.1,
                    "jurisdictions": ["eu"],
                },
                "fail",
            ),
            (
                "activo desconocido (NO se aprueba)",
                {
                    "ingredient_name": "Extracto propietario XR-9",
                    "concentration_pct": 1.0,
                    "jurisdictions": ["eu"],
                },
                "attention",
            ),
        ]:
            response = client.post("/api/v1/regulatory/check", json=payload, headers=auth)
            body = response.json() if response.status_code == 200 else {}
            got_summary = body.get("summary")
            detail = f"summary={got_summary} · {len(body.get('findings', []))} hallazgo(s)"
            report.check("/regulatory/check", label, 200, response.status_code, detail)
            if got_summary is not None:
                report.check(
                    "/regulatory/check",
                    f"  veredicto esperado '{expected_summary}'",
                    200 if got_summary == expected_summary else 0,
                    200,
                )

        # ── /voice/speech ───────────────────────────────────────────────────
        section("POST /api/v1/voice/speech  (fachada)")
        response = client.post("/api/v1/voice/speech", json={"text": "hola"}, headers=auth)
        expected = 503 if settings.tts_provider == "browser" else 200
        report.check(
            "/voice/speech",
            f"proveedor '{settings.tts_provider}'",
            expected,
            response.status_code,
            "503 es correcto: sintetiza el navegador" if expected == 503 else "",
        )

        # ── /reports ────────────────────────────────────────────────────────
        section("POST /api/v1/reports/{id}  (IA real)")
        report.check(
            "/reports",
            "simulacion inexistente",
            404,
            client.post("/api/v1/reports/no-existe", json={}, headers=auth).status_code,
        )

        db.tables["simulations"] = [simulation_row(user_id=OTHER_USER_ID)]
        report.check(
            "/reports",
            "simulacion de otro usuario (defensa en profundidad)",
            403,
            client.post(f"/api/v1/reports/{SIMULATION_ID}", json={}, headers=auth).status_code,
        )
        db.tables["simulations"] = [simulation_row()]

        if args.no_ai or not settings.ai_enabled:
            report.note("llamada al modelo omitida (--no-ai o sin clave configurada)")
        else:
            started = time.perf_counter()
            with client.stream(
                "POST", f"/api/v1/reports/{SIMULATION_ID}", json={}, headers=auth
            ) as stream:
                status = stream.status_code
                raw = b"".join(stream.iter_bytes()).decode("utf-8", errors="replace")
            elapsed = time.perf_counter() - started

            events: list[tuple[str, dict[str, Any]]] = []
            for block in raw.strip().split("\n\n"):
                name, payload = "", "{}"
                for line in block.splitlines():
                    if line.startswith("event: "):
                        name = line[7:]
                    elif line.startswith("data: "):
                        payload = line[6:]
                if name:
                    with contextlib.suppress(json.JSONDecodeError):
                        events.append((name, json.loads(payload)))

            names = [name for name, _ in events]
            text = "".join(data.get("text", "") for name, data in events if name == "delta")
            done = next((data for name, data in events if name == "done"), {})
            error = next((data for name, data in events if name == "error"), None)

            detail = f"eventos: {' -> '.join(dict.fromkeys(names))}"
            if done:
                detail += (
                    f"\ntokens: {done.get('input_tokens')} entrada / "
                    f"{done.get('output_tokens')} salida · {done.get('characters')} caracteres"
                )
            if done.get("truncated"):
                detail += (
                    f"\nAVISO: alcanzo el tope de {settings.ai_max_tokens} tokens "
                    "y el texto quedo cortado."
                )
            if error:
                detail += f"\nerror: {error.get('code')} {error.get('message')}"
            report.check("/reports", "genera reporte por SSE", 200, status, detail, elapsed)

            if text:
                report.check("/reports", "  emite texto", 200 if len(text) > 200 else 0, 200)
                report.check(
                    "/reports",
                    "  respeta la estructura del prompt",
                    200 if "## Resumen" in text else 0,
                    200,
                )
                report.check(
                    "/reports",
                    "  etiqueta la irritacion como heuristica",
                    200 if "heurístic" in text.lower() or "heuristic" in text.lower() else 0,
                    200,
                )
                report.check(
                    "/reports",
                    "  termina sin truncarse",
                    200 if not done.get("truncated") else 0,
                    200,
                    "" if not done.get("truncated") else "sube AI_MAX_TOKENS en .env",
                )
                prohibidas = [
                    frase
                    for frase in ("es seguro", "garantiza", "asegura la seguridad", "apto para uso")
                    if frase in text.lower()
                ]
                report.check(
                    "/reports",
                    "  NO sobredeclara seguridad",
                    200 if not prohibidas else 0,
                    200,
                    f"frases detectadas: {prohibidas}" if prohibidas else "",
                )

                print(f"\n{BOLD}  ── Reporte generado ──{RESET}")
                for line in text.strip().splitlines():
                    print(f"  {DIM}|{RESET} {line}")

            report.check(
                "/reports",
                "segunda vez sin force_regenerate",
                409,
                client.post(f"/api/v1/reports/{SIMULATION_ID}", json={}, headers=auth).status_code,
            )

        # ── /exports ────────────────────────────────────────────────────────
        section("GET /api/v1/exports/{id}.xlsx  (openpyxl real)")
        started = time.perf_counter()
        response = client.get(f"/api/v1/exports/{SIMULATION_ID}.xlsx", headers=auth)
        elapsed = time.perf_counter() - started
        detail = ""
        if response.status_code == 200:
            from io import BytesIO

            from openpyxl import load_workbook

            book = load_workbook(BytesIO(response.content))
            detail = f"{len(response.content) // 1024} KB · hojas: {', '.join(book.sheetnames)}"
        report.check(
            "/exports", "libro de la simulacion", 200, response.status_code, detail, elapsed
        )

        report.check(
            "/exports",
            "simulacion inexistente",
            404,
            client.get("/api/v1/exports/no-existe.xlsx", headers=auth).status_code,
        )

        # ── OpenAPI ─────────────────────────────────────────────────────────
        section("Contrato publico")
        response = client.get("/openapi.json")
        paths = sorted(response.json().get("paths", {}))
        report.check(
            "/openapi.json",
            "esquema disponible para el frontend",
            200,
            response.status_code,
            "\n".join(paths),
        )

    app.dependency_overrides.clear()
    return report.summary()


if __name__ == "__main__":
    raise SystemExit(main())
