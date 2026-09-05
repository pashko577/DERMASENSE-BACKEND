"""Configuracion comun de las pruebas.

Las variables de entorno se fijan **antes** de importar `app`, porque
`get_settings` esta cacheado y la primera lectura gana. El secreto de JWT de
aqui es de juguete y solo existe en memoria durante la prueba.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

os.environ.setdefault("SUPABASE_URL", "https://proyecto-de-prueba.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "clave-anonima-de-prueba")
os.environ.setdefault("SUPABASE_JWT_SECRET", "secreto-de-prueba-solo-para-tests")
os.environ.setdefault("ANTHROPIC_API_KEY", "clave-de-prueba")
os.environ.setdefault("ANTHROPIC_MODEL", "claude-sonnet-5")
os.environ.setdefault("TTS_PROVIDER", "browser")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("ENVIRONMENT", "development")

from fastapi.testclient import TestClient  # noqa: E402
from jose import jwt  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.deps import get_db  # noqa: E402
from app.main import create_app  # noqa: E402

USER_ID = "11111111-1111-4111-8111-111111111111"
OTHER_USER_ID = "22222222-2222-4222-8222-222222222222"
SIMULATION_ID = "33333333-3333-4333-8333-333333333333"


def make_token(
    *,
    user_id: str = USER_ID,
    expires_in_seconds: int = 3600,
    audience: str = "authenticated",
    secret: str | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": user_id,
        "aud": audience,
        "role": "authenticated",
        "email": "formulador@ejemplo.test",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in_seconds)).timestamp()),
    }
    claims.update(extra_claims or {})
    return jwt.encode(claims, secret or get_settings().supabase_jwt_secret, algorithm="HS256")


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token()}"}


class FakeDb:
    """Doble de `SupabaseRest` que registra lo que se le pide.

    Sustituye a PostgREST, no a RLS. Las pruebas que dependen de RLS de verdad
    solo pueden correr contra un Supabase real; lo que se verifica aqui es que el
    servicio no *dependa* de RLS para negar acceso.
    """

    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.counts: dict[str, int] = {}
        self.inserted: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[tuple[str, dict[str, Any]]] = []

    def seed(self, table: str, rows: list[dict[str, Any]]) -> None:
        self.rows[table] = rows

    async def select(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: dict[str, Any] | None = None,
        order: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.rows.get(table, [])
        return rows[:limit] if limit is not None else rows

    async def select_one(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        rows = await self.select(table, columns=columns, filters=filters, limit=1)
        return rows[0] if rows else None

    async def insert(
        self, table: str, row: dict[str, Any], *, returning: bool = True
    ) -> dict[str, Any] | None:
        self.inserted.append((table, row))
        return {**row, "id": "44444444-4444-4444-8444-444444444444"}

    async def delete(self, table: str, *, filters: dict[str, Any]) -> int:
        self.deleted.append((table, filters))
        self.rows[table] = []
        return 1

    async def count(self, table: str, *, filters: dict[str, Any] | None = None) -> int:
        return self.counts.get(table, 0)


def simulation_row(user_id: str = USER_ID) -> dict[str, Any]:
    """Fila realista de `simulations`, con `metrics` en camelCase como la escribe el motor."""
    return {
        "id": SIMULATION_ID,
        "user_id": user_id,
        "title": "Salicilico 2% en gel",
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
                "id": "55555555-5555-4555-8555-555555555555",
                "name": "Acido salicilico",
                "inciName": "Salicylic Acid",
                "molecularWeight": 138.12,
                "logP": 2.26,
                "pka": 2.97,
                "category": "BHA",
                "riskFlags": ["bha"],
            },
            "vehicle": {
                "id": "66666666-6666-4666-8666-666666666666",
                "name": "Gel hidroalcoholico",
                "enhancerFactor": 1.6,
            },
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


@pytest.fixture
def fake_db() -> FakeDb:
    return FakeDb()


@pytest.fixture
def client(fake_db: FakeDb):  # noqa: ANN201
    app = create_app()
    app.dependency_overrides[get_db] = lambda: fake_db
    with TestClient(app) as test_client:
        test_client.fake_db = fake_db  # type: ignore[attr-defined]
        yield test_client
    app.dependency_overrides.clear()
