"""Dependencias de peticion: identidad, cliente de base de datos y cuotas.

El servicio no emite sesiones. Recibe el JWT que Supabase Auth entrego al
frontend, verifica su firma y consulta PostgreSQL *como ese usuario*, de modo
que RLS (ADR-003) sigue siendo la ultima linea de defensa aunque un endpoint
tuviera un bug de filtrado. `service_role` no aparece en este archivo, y no debe
aparecer nunca.

Dos formas de firma conviven en Supabase y el servicio soporta ambas sin que el
resto del codigo se entere:

  - HS256 con `SUPABASE_JWT_SECRET` (proyectos legacy).
  - ES256/RS256 con JWT Signing Keys y JWKS publico (proyectos nuevos).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Annotated, Any

import httpx
from fastapi import Depends, Request
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError

from app.config import Settings, get_settings
from app.db.supabase import SupabaseRest
from app.errors import ApiError

_SYMMETRIC_ALGORITHMS = ["HS256"]
_ASYMMETRIC_ALGORITHMS = ["ES256", "RS256", "EdDSA"]


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Usuario autenticado y el token con el que llegara a PostgREST."""

    id: str
    email: str | None
    token: str
    claims: dict[str, Any]


class JwksCache:
    """Cache en proceso del JWKS del proyecto.

    Las claves rotan, asi que un `kid` desconocido fuerza una relectura, con un
    suelo de tiempo para que un token con `kid` basura no se convierta en un
    ariete contra el endpoint de Supabase.
    """

    def __init__(self, url: str, *, min_refresh_seconds: float = 60.0) -> None:
        self._url = url
        self._min_refresh_seconds = min_refresh_seconds
        self._keys: dict[str, dict[str, Any]] = {}
        self._fetched_at: float = 0.0

    async def get_key(self, kid: str | None) -> dict[str, Any]:
        if kid and kid in self._keys:
            return self._keys[kid]

        if self._keys and time.monotonic() - self._fetched_at < self._min_refresh_seconds:
            raise ApiError("UNAUTHORIZED", "El token fue firmado con una clave desconocida.")

        await self._refresh()

        if kid and kid in self._keys:
            return self._keys[kid]
        if not kid and len(self._keys) == 1:
            return next(iter(self._keys.values()))
        raise ApiError("UNAUTHORIZED", "El token fue firmado con una clave desconocida.")

    async def _refresh(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(self._url)
                response.raise_for_status()
                document = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ApiError(
                "UPSTREAM_ERROR",
                "No se pudieron obtener las claves publicas de Supabase.",
                {"reason": type(exc).__name__},
            ) from exc

        self._keys = {
            key["kid"]: key
            for key in document.get("keys", [])
            if isinstance(key, dict) and "kid" in key
        }
        self._fetched_at = time.monotonic()


class SlidingWindowLimiter:
    """Limitador por usuario, en memoria del proceso.

    Suficiente para una instancia unica: la cuota que de verdad importa —la de
    reportes de IA— se cuenta contra la base de datos, no aqui (ver
    `reports.py`), justamente porque este contador no sobrevive a un reinicio ni
    se comparte entre replicas.
    """

    def __init__(self, limit: int, window_seconds: float) -> None:
        self._limit = limit
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            retry_after = int(self._window - (now - hits[0])) + 1
            raise ApiError(
                "RATE_LIMITED",
                "Demasiadas peticiones. Reintenta en unos segundos.",
                {"retry_after_seconds": retry_after},
                headers={"Retry-After": str(retry_after)},
            )
        hits.append(now)


def get_jwks_cache(request: Request) -> JwksCache:
    cache: JwksCache = request.app.state.jwks_cache
    return cache


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization")
    if not header:
        raise ApiError(
            "UNAUTHORIZED",
            "Falta el encabezado Authorization.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(
            "UNAUTHORIZED",
            "El encabezado Authorization debe tener la forma: Bearer <token>.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


async def get_current_user(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    jwks: Annotated[JwksCache, Depends(get_jwks_cache)],
) -> CurrentUser:
    token = _bearer_token(request)

    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise ApiError("UNAUTHORIZED", "El token no es un JWT valido.") from exc

    algorithm = str(header.get("alg", ""))

    if algorithm in _SYMMETRIC_ALGORITHMS:
        if not settings.supabase_jwt_secret:
            raise ApiError(
                "UNAUTHORIZED",
                "El token usa firma simetrica y SUPABASE_JWT_SECRET no esta configurado.",
            )
        key: Any = settings.supabase_jwt_secret
        algorithms = _SYMMETRIC_ALGORITHMS
    elif algorithm in _ASYMMETRIC_ALGORITHMS:
        key = await jwks.get_key(header.get("kid"))
        algorithms = _ASYMMETRIC_ALGORITHMS
    else:
        raise ApiError("UNAUTHORIZED", f"Algoritmo de firma no admitido: {algorithm}.")

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience=settings.supabase_jwt_audience,
            options={"verify_aud": True},
        )
    except ExpiredSignatureError as exc:
        raise ApiError("UNAUTHORIZED", "La sesion expiro. Vuelve a iniciar sesion.") from exc
    except JWTError as exc:
        raise ApiError("UNAUTHORIZED", "El token no supero la verificacion de firma.") from exc

    subject = claims.get("sub")
    if not subject:
        raise ApiError("UNAUTHORIZED", "El token no identifica a ningun usuario.")

    # Un token anonimo pasa la verificacion de firma pero no representa a una
    # persona con fila en `profiles`: RLS lo rechazaria mas tarde y con peor
    # mensaje.
    if claims.get("is_anonymous") is True:
        raise ApiError("FORBIDDEN", "Esta operacion requiere una cuenta, no una sesion anonima.")

    return CurrentUser(id=str(subject), email=claims.get("email"), token=token, claims=claims)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def get_db(
    user: CurrentUserDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SupabaseRest:
    """Cliente de datos *por peticion*, con el JWT del usuario.

    Deliberadamente no hay singleton: un cliente compartido reutilizaria la
    identidad de quien lo creo, `auth.uid()` dejaria de coincidir y la tentacion
    de arreglarlo con `service_role` desactivaria RLS por completo.
    """
    return SupabaseRest(
        base_url=settings.postgrest_url,
        anon_key=settings.supabase_anon_key,
        access_token=user.token,
    )


DbDep = Annotated[SupabaseRest, Depends(get_db)]


def rate_limit(limit: int, window_seconds: float):  # noqa: ANN201 - fabrica de dependencias
    """Construye una dependencia de limite por usuario para un endpoint."""
    limiter = SlidingWindowLimiter(limit, window_seconds)

    async def _dependency(user: CurrentUserDep) -> None:
        limiter.check(user.id)

    return _dependency


def public_rate_limit(limit: int, window_seconds: float):  # noqa: ANN201
    """Limite para endpoints sin sesion, contado por direccion de origen.

    Los endpoints publicos de este servicio no gastan dinero ni tocan datos de
    usuario, pero siguen consumiendo CPU. La IP es un identificador debil
    —detras de un proxy todos comparten una— y por eso el limite es generoso:
    frena un bucle accidental, no a un atacante decidido.
    """
    limiter = SlidingWindowLimiter(limit, window_seconds)

    async def _dependency(request: Request) -> None:
        forwarded = request.headers.get("x-forwarded-for", "")
        origin = forwarded.split(",")[0].strip() or (
            request.client.host if request.client else "desconocido"
        )
        limiter.check(origin)

    return _dependency
