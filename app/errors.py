"""Formato unico de error, compartido con el frontend (TRD §3).

    { "error": { "code": "...", "message": "...", "details": {} } }

El codigo es parte del contrato publico: la UI decide con el si ofrece un
reintento (`AI_UNAVAILABLE`), pide iniciar sesion (`UNAUTHORIZED`) o marca un
campo del formulario (`VALIDATION_ERROR`).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

ErrorCode = Literal[
    "VALIDATION_ERROR",
    "UNAUTHORIZED",
    "FORBIDDEN",
    "NOT_FOUND",
    "CONFLICT",
    "RATE_LIMITED",
    "AI_UNAVAILABLE",
    "DEPENDENCY_UNAVAILABLE",
    "UPSTREAM_ERROR",
    "INTERNAL_ERROR",
]

_STATUS_BY_CODE: dict[str, int] = {
    "VALIDATION_ERROR": status.HTTP_400_BAD_REQUEST,
    "UNAUTHORIZED": status.HTTP_401_UNAUTHORIZED,
    "FORBIDDEN": status.HTTP_403_FORBIDDEN,
    "NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "CONFLICT": status.HTTP_409_CONFLICT,
    "RATE_LIMITED": status.HTTP_429_TOO_MANY_REQUESTS,
    "AI_UNAVAILABLE": status.HTTP_503_SERVICE_UNAVAILABLE,
    "DEPENDENCY_UNAVAILABLE": status.HTTP_503_SERVICE_UNAVAILABLE,
    "UPSTREAM_ERROR": status.HTTP_502_BAD_GATEWAY,
    "INTERNAL_ERROR": status.HTTP_500_INTERNAL_SERVER_ERROR,
}


class ApiError(Exception):
    """Error de dominio con codigo estable y detalle opcional."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers or {}

    @property
    def http_status(self) -> int:
        return _STATUS_BY_CODE.get(self.code, status.HTTP_500_INTERNAL_SERVER_ERROR)

    def payload(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


def error_response(error: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=error.http_status,
        content=error.payload(),
        headers=error.headers or None,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic emite objetos no serializables (excepciones en `ctx`);
        # `errors()` de FastAPI ya viene saneado, pero el `input` puede traer
        # bytes, asi que se fuerza a texto.
        details = [
            {
                "loc": [str(part) for part in item.get("loc", ())],
                "msg": item.get("msg", ""),
                "type": item.get("type", ""),
            }
            for item in exc.errors()
        ]
        return error_response(
            ApiError("VALIDATION_ERROR", "El payload no cumple el contrato.", {"issues": details})
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        # El mensaje interno no viaja al cliente: podria contener fragmentos de
        # una consulta o de una clave.
        return error_response(
            ApiError("INTERNAL_ERROR", "Error interno del servicio.", {"type": type(exc).__name__})
        )
