"""Ingesta desde PubChem PUG-REST.

Dos restricciones dan forma a este modulo:

1. El NIH pide no exceder **5 peticiones por segundo**. Un token bucket lo
   garantiza a nivel de proceso; superarlo devuelve 503 desde su lado y bloquea
   la IP, que durante una demo es un fallo caro.
2. PubChem devuelve casi siempre `XLogP3`, **calculado por computadora, no
   medido**. Un error de 0.5 unidades en logP desplaza `log Kp` en 0.35: un
   factor de mas de 2 en permeabilidad. Por eso este servicio *nunca* promueve
   un valor a `verified` por si solo: propone candidatos, y la curacion final es
   manual (DATA_SOURCES §3.2).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

from app.errors import ApiError
from app.schemas.ingredient import FieldSource, PubChemCandidate

BASE_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"

# Propiedades que el motor necesita, en el orden en que PUG-REST las devuelve.
_PROPERTIES = [
    "MolecularFormula",
    "MolecularWeight",
    "XLogP",
    "TPSA",
    "HBondDonorCount",
    "HBondAcceptorCount",
    "CanonicalSMILES",
    "IUPACName",
]

_XLOGP_WARNING = (
    "XLogP3 es un valor calculado por computadora, no medido experimentalmente. "
    "Verifica el logP en la ficha de PubChem antes de curar este activo: un error "
    "de 0.5 unidades desplaza log Kp en 0.35."
)

RESOLVE_NOTE = (
    "Candidatos propuestos, no curados. Ningun campo se promueve a 'verified' de "
    "forma automatica (DATA_SOURCES §3.2)."
)


class TokenBucket:
    """Limitador de tasa asincrono para no exceder la cuota del NIH."""

    def __init__(self, rate_per_second: float, capacity: float | None = None) -> None:
        self._rate = rate_per_second
        self._capacity = capacity if capacity is not None else rate_per_second
        self._tokens = self._capacity
        self._updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._updated_at
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                self._updated_at = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) / self._rate)


class DiskCache:
    """Cache en disco de respuestas de PubChem.

    Las propiedades fisicoquimicas de una molecula no cambian, asi que la cache
    no expira: existe para que una demo repetida no consuma la cuota del NIH.
    """

    def __init__(self, directory: str) -> None:
        self._dir = Path(directory)

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self._dir / f"{digest}.json"

    def get(self, key: str) -> Any | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def set(self, key: str, value: Any) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._path(key).write_text(json.dumps(value), encoding="utf-8")
        except OSError:
            # Una cache que no puede escribir degrada el rendimiento, no la
            # correccion: seguir sin ella es preferible a fallar la peticion.
            pass


class PubChemClient:
    def __init__(
        self,
        *,
        requests_per_second: float = 5.0,
        timeout_seconds: float = 15.0,
        cache_dir: str = ".cache/pubchem",
    ) -> None:
        self._bucket = TokenBucket(requests_per_second)
        self._timeout = timeout_seconds
        self._cache = DiskCache(cache_dir)

    async def _get_json(self, path: str) -> Any:
        cached = self._cache.get(path)
        if cached is not None:
            return cached

        await self._bucket.acquire()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(f"{BASE_URL}{path}")
        except httpx.HTTPError as exc:
            raise ApiError(
                "UPSTREAM_ERROR",
                "PubChem no respondio.",
                {"reason": type(exc).__name__},
            ) from exc

        if response.status_code == 404:
            raise ApiError("NOT_FOUND", "PubChem no encontro ninguna coincidencia.")
        if response.status_code == 503:
            raise ApiError(
                "RATE_LIMITED",
                "PubChem esta limitando las peticiones. Reintenta en unos segundos.",
            )
        if response.status_code >= 400:
            raise ApiError(
                "UPSTREAM_ERROR",
                "PubChem rechazo la consulta.",
                {"status": response.status_code},
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiError("UPSTREAM_ERROR", "Respuesta ilegible de PubChem.") from exc

        self._cache.set(path, payload)
        return payload

    async def resolve_by_name(self, name: str, limit: int = 5) -> list[PubChemCandidate]:
        """Nombre → candidatos con CID estable."""
        cids_payload = await self._get_json(f"/compound/name/{_quote(name)}/cids/JSON")
        cids = (cids_payload.get("IdentifierList") or {}).get("CID") or []
        if not cids:
            return []

        selected = [int(cid) for cid in cids[:limit]]
        joined = ",".join(str(cid) for cid in selected)
        properties = ",".join(_PROPERTIES)
        payload = await self._get_json(f"/compound/cid/{joined}/property/{properties}/JSON")

        rows = (payload.get("PropertyTable") or {}).get("Properties") or []
        return [_to_candidate(row, fallback_name=name) for row in rows]

    async def fetch_by_cid(self, cid: int) -> PubChemCandidate | None:
        properties = ",".join(_PROPERTIES)
        payload = await self._get_json(f"/compound/cid/{cid}/property/{properties}/JSON")
        rows = (payload.get("PropertyTable") or {}).get("Properties") or []
        if not rows:
            return None
        return _to_candidate(rows[0], fallback_name=f"CID {cid}")


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_candidate(row: dict[str, Any], *, fallback_name: str) -> PubChemCandidate:
    cid = _as_int(row.get("CID")) or 0
    reference = f"CID {cid}"

    xlogp = _as_float(row.get("XLogP"))
    warnings: list[str] = []
    sources: dict[str, FieldSource] = {}

    if row.get("MolecularWeight") is not None:
        # El peso molecular se deriva de la formula: es exacto, no una medida.
        sources["molecular_weight"] = FieldSource(
            db="PubChem", id=reference, type="experimental", level="verified"
        )
    if xlogp is not None:
        sources["log_p"] = FieldSource(
            db="PubChem", id=reference, type="calculated", level="estimated"
        )
        warnings.append(_XLOGP_WARNING)
    else:
        warnings.append("PubChem no publica logP para este compuesto; hay que curarlo a mano.")

    return PubChemCandidate(
        cid=cid,
        name=row.get("IUPACName") or fallback_name,
        iupac_name=row.get("IUPACName"),
        molecular_formula=row.get("MolecularFormula"),
        molecular_weight=_as_float(row.get("MolecularWeight")),
        xlogp=xlogp,
        tpsa=_as_float(row.get("TPSA")),
        h_bond_donors=_as_int(row.get("HBondDonorCount")),
        h_bond_acceptors=_as_int(row.get("HBondAcceptorCount")),
        canonical_smiles=row.get("CanonicalSMILES") or row.get("ConnectivitySMILES"),
        sources=sources,
        warnings=warnings,
    )
