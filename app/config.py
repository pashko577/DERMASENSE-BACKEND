"""Configuracion del servicio.

Falla al arrancar si falta una clave estructural (README §8). La clave de
Anthropic es la excepcion deliberada: su ausencia degrada a `503 AI_UNAVAILABLE`
en tiempo de peticion y deja una advertencia en el log de arranque, porque las
metricas de la simulacion deben seguir siendo utilizables sin IA
(docs/AI_PROMPTS.md §4).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

TtsProvider = Literal["browser", "elevenlabs", "azure"]
AiProvider = Literal["anthropic", "openrouter", "groq"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        populate_by_name=True,
        extra="ignore",
    )

    # ── Supabase ───────────────────────────────────────────────────────────
    supabase_url: str
    supabase_anon_key: str

    # Verificacion de firma del JWT. Dos vias mutuamente excluyentes:
    #   - simetrica  (HS256): SUPABASE_JWT_SECRET
    #   - asimetrica (ES256/RS256): JWKS publico del proyecto
    supabase_jwt_secret: str | None = None
    supabase_jwks_url: str | None = None

    # `authenticated` es la audiencia que emite Supabase Auth para un usuario
    # con sesion iniciada.
    supabase_jwt_audience: str = "authenticated"

    # ── Proveedor de IA ────────────────────────────────────────────────────
    # El modelo es el mismo en los dos caminos (`claude-sonnet-5`, TRD §1); lo
    # que cambia es por donde sale la peticion.
    ai_provider: AiProvider = "anthropic"

    # docs/AI_PROMPTS.md fija `max_tokens: 1200`, pero medido contra
    # claude-sonnet-5 el reporte en espanol alcanza ese tope y queda cortado a
    # mitad de frase: el prompt pide cinco secciones en <=400 palabras y el
    # modelo escribe unas 450, que en espanol son ~1200 tokens justos. Se sube a
    # 2000 para dar margen; son $0,008 mas por reporte y evita el unico fallo
    # que se ve roto en pantalla sin producir ningun error.
    ai_max_tokens: int = 2000
    ai_timeout_seconds: float = 60.0

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"

    openrouter_api_key: str | None = None
    # OpenRouter prefija el proveedor en el identificador del modelo.
    openrouter_model: str = "anthropic/claude-sonnet-5"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_site_url: str = "http://localhost:3000"
    openrouter_app_name: str = "DERMASENSE"

    # Groq sirve modelos abiertos (no Claude) con latencia muy baja y capa
    # gratuita. Util como alternativa o respaldo; ver la nota de calidad en
    # README §4 antes de usarlo para el reporte tecnico.
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # Cuota de reportes por usuario y ventana (docs/AI_PROMPTS.md §5).
    ai_reports_daily_quota: int = 20

    # ── Voz ────────────────────────────────────────────────────────────────
    tts_provider: TtsProvider = "browser"
    tts_api_key: str | None = None
    tts_azure_region: str | None = None

    # ── PubChem ────────────────────────────────────────────────────────────
    # El NIH pide no exceder 5 peticiones por segundo.
    pubchem_max_requests_per_second: float = 5.0
    pubchem_timeout_seconds: float = 15.0
    pubchem_cache_dir: str = ".cache/pubchem"

    # ── Servicio ───────────────────────────────────────────────────────────
    environment: Literal["development", "production"] = "development"
    version: str = "0.1.0"

    # Se lee como texto y se parte a mano. Declararlo como `list[str]` haria que
    # pydantic-settings intentara decodificar el valor como JSON antes de
    # cualquier validador, y "http://a,http://b" no es JSON valido.
    cors_origins_raw: str = Field(default="http://localhost:3000", alias="CORS_ORIGINS")

    @field_validator("supabase_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def _check_tts_credentials(self) -> Settings:
        if self.tts_provider != "browser" and not self.tts_api_key:
            raise ValueError(
                f"TTS_PROVIDER='{self.tts_provider}' requiere TTS_API_KEY. "
                "Usa TTS_PROVIDER=browser para la sintesis local sin clave."
            )
        if self.tts_provider == "azure" and not self.tts_azure_region:
            raise ValueError("TTS_PROVIDER='azure' requiere TTS_AZURE_REGION.")
        return self

    @property
    def cors_origins(self) -> list[str]:
        """Dominios del frontend autorizados, separados por coma."""
        return [item.strip() for item in self.cors_origins_raw.split(",") if item.strip()]

    @property
    def jwks_url(self) -> str:
        """URL de descubrimiento de claves publicas del proyecto Supabase."""
        return self.supabase_jwks_url or f"{self.supabase_url}/auth/v1/.well-known/jwks.json"

    @property
    def postgrest_url(self) -> str:
        return f"{self.supabase_url}/rest/v1"

    @property
    def ai_enabled(self) -> bool:
        """Si el proveedor activo tiene credencial.

        Se comprueba solo el proveedor seleccionado: tener una clave de Anthropic
        no habilita reportes cuando `AI_PROVIDER=openrouter`, y anunciar lo
        contrario en `/health` seria peor que no anunciar nada.
        """
        if self.ai_provider == "openrouter":
            return bool(self.openrouter_api_key)
        if self.ai_provider == "groq":
            return bool(self.groq_api_key)
        return bool(self.anthropic_api_key)

    @property
    def ai_model(self) -> str:
        """Identificador del modelo tal como lo espera el proveedor activo."""
        if self.ai_provider == "openrouter":
            return self.openrouter_model
        if self.ai_provider == "groq":
            return self.groq_model
        return self.anthropic_model

    @property
    def ai_key_variable(self) -> str:
        """Nombre de la variable que hay que definir. Para mensajes de error utiles."""
        return {
            "openrouter": "OPENROUTER_API_KEY",
            "groq": "GROQ_API_KEY",
        }.get(self.ai_provider, "ANTHROPIC_API_KEY")

    @property
    def ai_api_key(self) -> str | None:
        """Credencial del proveedor activo."""
        return {
            "openrouter": self.openrouter_api_key,
            "groq": self.groq_api_key,
        }.get(self.ai_provider, self.anthropic_api_key)

    @property
    def ai_base_url(self) -> str:
        """Endpoint compatible con OpenAI del proveedor activo."""
        return self.groq_base_url if self.ai_provider == "groq" else self.openrouter_base_url

    @property
    def ai_extra_headers(self) -> dict[str, str]:
        """Cabeceras de atribucion. Solo OpenRouter las usa; Groq las ignora."""
        if self.ai_provider == "openrouter":
            return {
                "HTTP-Referer": self.openrouter_site_url,
                "X-Title": self.openrouter_app_name,
            }
        return {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
