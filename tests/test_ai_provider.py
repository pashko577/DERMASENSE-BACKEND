"""Seleccion de proveedor de IA y deteccion de truncamiento.

Ninguna de estas pruebas sale a la red: se comprueba el cableado, no el modelo.
La llamada real vive en `scripts/debug_routes.py`, que si gasta tokens y por eso
no forma parte de la suite.
"""

from __future__ import annotations

from app.config import Settings
from app.services.ai import build_report_generator
from app.services.claude import ClaudeService, StreamAccounting
from app.services.openrouter import OpenRouterService

# Todo explicito, incluidos los valores nulos: `.env` y las variables de entorno
# de la maquina alimentan `Settings`, y una prueba que los herede pasa o falla
# segun como este configurado quien la ejecuta.
BASE = {
    "supabase_url": "https://proyecto.supabase.co",
    "supabase_anon_key": "anon",
    "supabase_jwt_secret": "secreto",
    "ai_provider": "anthropic",
    "anthropic_api_key": None,
    "openrouter_api_key": None,
}


def settings(**overrides) -> Settings:  # noqa: ANN003
    return Settings(**{**BASE, **overrides})  # type: ignore[arg-type]


def test_el_proveedor_por_defecto_es_anthropic() -> None:
    # Se lee de la definicion del campo, no de una instancia: una instancia
    # tomaria el valor de `.env` de esta maquina.
    assert Settings.model_fields["ai_provider"].default == "anthropic"


def test_anthropic_cuando_se_selecciona() -> None:
    generator = build_report_generator(settings(anthropic_api_key="clave"))
    assert isinstance(generator, ClaudeService)


def test_openrouter_cuando_se_selecciona() -> None:
    generator = build_report_generator(
        settings(ai_provider="openrouter", openrouter_api_key="clave")
    )
    assert isinstance(generator, OpenRouterService)


def test_la_clave_de_un_proveedor_no_habilita_al_otro() -> None:
    """Tener clave de Anthropic no habilita reportes si el proveedor es OpenRouter.

    Anunciar lo contrario en /health seria peor que no anunciar nada: el
    frontend ofreceria un boton que siempre devuelve 503.
    """
    con_anthropic = settings(ai_provider="openrouter", anthropic_api_key="clave")
    assert con_anthropic.ai_enabled is False
    assert con_anthropic.ai_key_variable == "OPENROUTER_API_KEY"

    con_openrouter = settings(ai_provider="anthropic", openrouter_api_key="clave")
    assert con_openrouter.ai_enabled is False
    assert con_openrouter.ai_key_variable == "ANTHROPIC_API_KEY"


def test_el_identificador_del_modelo_cambia_con_el_proveedor() -> None:
    # Es el mismo modelo (TRD §1); solo cambia como lo nombra cada proveedor.
    assert settings(ai_provider="anthropic").ai_model == "claude-sonnet-5"
    assert settings(ai_provider="openrouter").ai_model == "anthropic/claude-sonnet-5"


def test_el_tope_de_tokens_deja_margen_sobre_el_reporte_medido() -> None:
    """Medido: un reporte real en espanol ocupa ~1360 tokens con sonnet-5.

    docs/AI_PROMPTS.md fijaba 1200 y el texto salia cortado a mitad de frase.
    Si alguien vuelve a bajarlo por debajo de lo medido, esta prueba lo detiene.
    """
    assert settings().ai_max_tokens >= 1500


def test_la_contabilidad_arranca_sin_marcar_truncamiento() -> None:
    accounting = StreamAccounting()
    assert accounting.truncated is False
    assert accounting.text == ""

    accounting.chunks.append("hola")
    assert accounting.text == "hola"
