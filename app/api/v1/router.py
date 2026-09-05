"""Agregador de routers bajo /api/v1."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    chat,
    descriptors,
    exports,
    ingredients,
    regulatory,
    reports,
    validation,
    voice,
)

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(chat.router)
api_router.include_router(descriptors.router)
api_router.include_router(ingredients.router)
api_router.include_router(reports.router)
api_router.include_router(voice.router)
api_router.include_router(exports.router)
api_router.include_router(regulatory.router)
api_router.include_router(validation.router)
