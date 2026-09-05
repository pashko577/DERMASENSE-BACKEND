"""Acceso a PostgreSQL a traves de PostgREST, con la identidad del usuario.

Se habla con la API REST de Supabase por HTTP en vez de usar el SDK oficial por
una razon concreta: el SDK esta pensado para un cliente de larga vida con una
sesion propia, y aqui hace falta exactamente lo contrario —un cliente efimero
que porta el JWT de *esta* peticion y muere con ella—. Un singleton compartido
haria que `auth.uid()` dejara de corresponder al solicitante, que es la puerta
por la que se cuela `service_role`.

`apikey` es la clave anonima (publica, protegida por RLS). `Authorization` es el
carnet del usuario, y es el que PostgREST convierte en `auth.uid()`.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.errors import ApiError

JsonDict = dict[str, Any]

# Codigos de PostgreSQL que tienen una traduccion util para el cliente.
_PG_UNIQUE_VIOLATION = "23505"
_PG_FOREIGN_KEY_VIOLATION = "23503"
_PG_RLS_VIOLATION = "42501"


class SupabaseRest:
    """Cliente PostgREST de una sola peticion."""

    def __init__(
        self,
        *,
        base_url: str,
        anon_key: str,
        access_token: str,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._headers = {
            "apikey": anon_key,
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    # ── Primitivas ─────────────────────────────────────────────────────────

    async def select(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: JsonDict | None = None,
        order: str | None = None,
        limit: int | None = None,
    ) -> list[JsonDict]:
        params: dict[str, str] = {"select": columns}
        for key, value in (filters or {}).items():
            params[key] = str(value)
        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = str(limit)

        payload = await self._request("GET", f"/{table}", params=params)
        return payload if isinstance(payload, list) else []

    async def select_one(
        self,
        table: str,
        *,
        columns: str = "*",
        filters: JsonDict | None = None,
    ) -> JsonDict | None:
        rows = await self.select(table, columns=columns, filters=filters, limit=1)
        return rows[0] if rows else None

    async def insert(self, table: str, row: JsonDict, *, returning: bool = True) -> JsonDict | None:
        headers = {"Prefer": "return=representation" if returning else "return=minimal"}
        payload = await self._request("POST", f"/{table}", json=[row], headers=headers)
        if isinstance(payload, list) and payload:
            return payload[0]
        return None

    async def delete(self, table: str, *, filters: JsonDict) -> int:
        """Borra filas y devuelve cuantas se borraron.

        `filters` es obligatorio: un borrado sin filtro solo estaria acotado por
        RLS, y apoyarse en eso para no vaciar una tabla es demasiado fragil.
        """
        if not filters:
            raise ApiError("VALIDATION_ERROR", "Un borrado requiere al menos un filtro.")

        params = {key: str(value) for key, value in filters.items()}
        payload = await self._request(
            "DELETE",
            f"/{table}",
            params=params,
            headers={"Prefer": "return=representation"},
        )
        return len(payload) if isinstance(payload, list) else 0

    async def count(self, table: str, *, filters: JsonDict | None = None) -> int:
        """Cuenta exacta usando el encabezado `Content-Range` de PostgREST."""
        params: dict[str, str] = {"select": "id"}
        for key, value in (filters or {}).items():
            params[key] = str(value)

        response = await self._send(
            "GET",
            f"/{table}",
            params=params,
            headers={"Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"},
        )
        content_range = response.headers.get("content-range", "")
        _, _, total = content_range.partition("/")
        return int(total) if total.isdigit() else 0

    # ── Transporte ─────────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        response = await self._send(method, path, params=params, json=json, headers=headers)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError("UPSTREAM_ERROR", "Respuesta ilegible de la base de datos.") from exc

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        merged = {**self._headers, **(headers or {})}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method,
                    f"{self._base_url}{path}",
                    params=params,
                    json=json,
                    headers=merged,
                )
        except httpx.HTTPError as exc:
            raise ApiError(
                "UPSTREAM_ERROR",
                "La base de datos no respondio.",
                {"reason": type(exc).__name__},
            ) from exc

        if response.status_code >= 400:
            raise self._translate(response)
        return response

    @staticmethod
    def _translate(response: httpx.Response) -> ApiError:
        try:
            body = response.json()
        except ValueError:
            body = {}

        code = body.get("code") if isinstance(body, dict) else None
        message = body.get("message") if isinstance(body, dict) else None

        if response.status_code in (401, 403) or code == _PG_RLS_VIOLATION:
            # RLS denegando es indistinguible de "no existe" desde fuera, y esa
            # ambiguedad es deseable: no confirma la existencia del recurso.
            return ApiError("FORBIDDEN", "El usuario no tiene acceso a este recurso.")
        if code == _PG_UNIQUE_VIOLATION:
            return ApiError("CONFLICT", "El recurso ya existe.", {"pg_code": code})
        if code == _PG_FOREIGN_KEY_VIOLATION:
            return ApiError("VALIDATION_ERROR", "Referencia inexistente.", {"pg_code": code})

        return ApiError(
            "UPSTREAM_ERROR",
            "La base de datos rechazo la operacion.",
            {"status": response.status_code, "pg_code": code, "pg_message": message},
        )
