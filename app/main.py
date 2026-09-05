"""Punto de entrada de la API.

Los objetos caros y sin estado de usuario —cache de JWKS, cliente de PubChem,
cliente de Anthropic, sintetizador de voz— se construyen una vez en el lifespan y
viven en `app.state`. El unico que se crea por peticion es el cliente de base de
datos, y esa asimetria es deliberada: es el que porta la identidad del
solicitante (ver `deps.get_db`).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.config import Settings, get_settings
from app.deps import JwksCache
from app.errors import register_exception_handlers
from app.services.ai import build_report_generator
from app.services.pubchem import PubChemClient
from app.services.rdkit_descriptors import rdkit_version
from app.services.tts import build_synthesizer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("dermasense")

DESCRIPTION = """\
Capa cientifica y documental de DERMASENSE.

**Este servicio no simula.** El motor de difusion vive en el navegador
(`packages/engine`, ADR-001) y aqui no se reimplementa: dos fuentes de verdad
para el mismo numero divergirian, y una divergencia numerica es el fallo mas caro
posible para un producto cuya propuesta entera es la trazabilidad.

Lo que si vive aqui: descriptores con RDKit, ingesta de PubChem, reportes con
Claude, exportacion a Excel, reglas regulatorias y la fachada de voz.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    app.state.settings = settings
    app.state.jwks_cache = JwksCache(settings.jwks_url)
    app.state.pubchem = PubChemClient(
        requests_per_second=settings.pubchem_max_requests_per_second,
        timeout_seconds=settings.pubchem_timeout_seconds,
        cache_dir=settings.pubchem_cache_dir,
    )
    app.state.claude = build_report_generator(settings)
    app.state.tts = build_synthesizer(settings)

    _log_startup_warnings(settings)
    yield


def _log_startup_warnings(settings: Settings) -> None:
    """Las degradaciones se anuncian al arrancar, no al fallar la primera peticion."""
    if not settings.ai_enabled:
        logger.warning(
            "%s no esta configurada: POST /reports respondera 503. "
            "Las metricas de simulacion no se ven afectadas.",
            settings.ai_key_variable,
        )
    else:
        logger.info(
            "Proveedor de IA: %s · modelo: %s", settings.ai_provider, settings.ai_model
        )
    if not settings.supabase_jwt_secret:
        logger.info(
            "SUPABASE_JWT_SECRET no esta configurado: los tokens se verificaran "
            "contra el JWKS del proyecto (%s).",
            settings.jwks_url,
        )
    if rdkit_version() is None:
        logger.warning(
            "RDKit no esta instalado: POST /descriptors respondera 503. "
            "El resto de la API funciona con normalidad."
        )
    if settings.environment == "production" and any(
        origin.startswith("http://localhost") for origin in settings.cors_origins
    ):
        logger.warning("CORS_ORIGINS incluye localhost en produccion.")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="DERMASENSE Backend",
        description=DESCRIPTION,
        version=settings.version,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # el JWT viaja en el encabezado, no en cookies
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["Content-Disposition"],
        max_age=600,
    )

    register_exception_handlers(app)
    app.include_router(api_router)

    @app.get("/health", tags=["salud"], summary="Sonda de vida y capacidades")
    async def health() -> dict[str, Any]:
        # La sonda declara que capacidades estan realmente disponibles: un
        # servicio vivo con RDKit ausente no es lo mismo que uno completo, y
        # descubrirlo en la primera peticion del usuario es tarde.
        return {
            "status": "ok",
            "version": settings.version,
            "environment": settings.environment,
            "capabilities": {
                "descriptors": rdkit_version() is not None,
                "reports": settings.ai_enabled,
                "voice_provider": settings.tts_provider,
                "exports": True,
                "regulatory": True,
            },
            "rdkit_version": rdkit_version(),
            "ai_provider": settings.ai_provider,
            "model": settings.ai_model,
        }

    return app


app = create_app()
