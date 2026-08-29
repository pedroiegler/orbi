"""Configuracao da aplicacao.

Tudo que muda entre ambientes vem daqui, nunca de constante espalhada no codigo.
Segredos chegam por variavel de ambiente e nunca sao logados.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]
LLMProviderName = Literal["gemini", "anthropic", "openai", "rule_based"]
EmbeddingProviderName = Literal["hashing", "openai"]

EMBEDDING_DIMENSIONS = 768
"""Fixo em 768: metade do armazenamento e da latencia, qualidade equivalente
para nomes curtos de catalogo (ORBI.md secao 7)."""


class Settings(BaseSettings):
    """Configuracao unica do processo."""

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- aplicacao -------------------------------------------------------
    env: Environment = Field(default="development", alias="ORBI_ENV")
    log_level: str = Field(default="INFO", alias="ORBI_LOG_LEVEL")
    timezone: str = Field(default="America/Sao_Paulo", alias="ORBI_TIMEZONE")
    locale: str = Field(default="pt_BR", alias="ORBI_LOCALE")

    secret_key: SecretStr = Field(default=SecretStr(""), alias="ORBI_SECRET_KEY")

    # --- banco -----------------------------------------------------------
    database_url: str = Field(
        default="postgresql+psycopg://orbi_app:orbi-dev@localhost:5433/orbi",
        alias="ORBI_DATABASE_URL",
    )
    database_admin_url: str = Field(
        default="postgresql+psycopg://postgres:orbi-dev@localhost:5433/orbi",
        alias="ORBI_DATABASE_ADMIN_URL",
    )
    database_pool_size: int = Field(default=5, alias="ORBI_DATABASE_POOL_SIZE")
    database_echo: bool = Field(default=False, alias="ORBI_DATABASE_ECHO")

    # --- orcamento de tempo (ORBI.md secao 15) ---------------------------
    deadline_total_ms: int = Field(default=10_000, alias="ORBI_DEADLINE_TOTAL_MS")
    llm_timeout_ms: int = Field(default=4_000, alias="ORBI_LLM_TIMEOUT_MS")
    erp_timeout_ms: int = Field(default=6_000, alias="ORBI_ERP_TIMEOUT_MS")
    slow_reply_threshold_ms: int = Field(default=2_500, alias="ORBI_SLOW_REPLY_THRESHOLD_MS")

    # --- LLM -------------------------------------------------------------
    llm_primary: LLMProviderName = Field(default="gemini", alias="ORBI_LLM_PRIMARY")
    llm_fallback: LLMProviderName | None = Field(default=None, alias="ORBI_LLM_FALLBACK")
    gemini_api_key: SecretStr = Field(default=SecretStr(""), alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
    anthropic_api_key: SecretStr = Field(default=SecretStr(""), alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-5", alias="ANTHROPIC_MODEL")
    openai_api_key: SecretStr = Field(default=SecretStr(""), alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4.1", alias="OPENAI_MODEL")

    llm_rendering_enabled: bool = Field(default=False, alias="ORBI_LLM_RENDERING_ENABLED")
    """Feature flag da secao 6.14. Fica falso: a resposta e template."""

    # --- embeddings ------------------------------------------------------
    embedding_provider: EmbeddingProviderName = Field(
        default="hashing", alias="ORBI_EMBEDDING_PROVIDER"
    )
    embedding_model: str = Field(default="text-embedding-3-small", alias="ORBI_EMBEDDING_MODEL")

    # --- canal WhatsApp --------------------------------------------------
    whatsapp_api_version: str = Field(default="v21.0", alias="ORBI_WHATSAPP_API_VERSION")
    whatsapp_verify_token: SecretStr = Field(
        default=SecretStr(""), alias="ORBI_WHATSAPP_VERIFY_TOKEN"
    )
    whatsapp_app_secret: SecretStr = Field(default=SecretStr(""), alias="ORBI_WHATSAPP_APP_SECRET")

    # --- canal de operacao ----------------------------------------------
    ops_phone: str = Field(default="", alias="ORBI_OPS_PHONE")
    ops_phone_number_id: str = Field(default="", alias="ORBI_OPS_PHONE_NUMBER_ID")
    ops_access_token: SecretStr = Field(default=SecretStr(""), alias="ORBI_OPS_ACCESS_TOKEN")

    # --- observabilidade -------------------------------------------------
    langfuse_public_key: SecretStr = Field(default=SecretStr(""), alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: SecretStr = Field(default=SecretStr(""), alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="https://cloud.langfuse.com", alias="LANGFUSE_HOST")

    @field_validator("llm_fallback", mode="before")
    @classmethod
    def _empty_fallback_is_none(cls, value: object) -> object:
        # Variavel de ambiente vazia significa "sem fallback", nao valor invalido.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def require_secret_key(self) -> str:
        """Chave de cifragem das credenciais de ERP.

        Falha alto: sem ela, credencial de cliente nao pode ser lida nem gravada.
        """
        key = self.secret_key.get_secret_value()
        if not key:
            raise RuntimeError(
                "ORBI_SECRET_KEY nao configurada. "
                'Gere com: python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        return key

    def validate_for_production(self) -> list[str]:
        """Regras que so valem em producao. Devolve a lista de problemas."""
        problems: list[str] = []
        if not self.secret_key.get_secret_value():
            problems.append("ORBI_SECRET_KEY ausente")
        if self.llm_primary == "rule_based":
            problems.append("ORBI_LLM_PRIMARY=rule_based nao e permitido em producao")
        if self.llm_fallback is None:
            problems.append("ORBI_LLM_FALLBACK ausente: failover exige dois fabricantes")
        elif _manufacturer(self.llm_primary) == _manufacturer(self.llm_fallback):
            problems.append(
                "primario e fallback do mesmo fabricante: a queda seria correlacionada"
            )
        if self.llm_rendering_enabled:
            problems.append("llm_rendering_enabled deve ser falso: a resposta e template")
        if not self.whatsapp_verify_token.get_secret_value():
            problems.append("ORBI_WHATSAPP_VERIFY_TOKEN ausente")
        if not self.whatsapp_app_secret.get_secret_value():
            problems.append(
                "ORBI_WHATSAPP_APP_SECRET ausente: webhook sem verificacao de assinatura"
            )
        return problems


def _manufacturer(provider: LLMProviderName) -> str:
    """Fabricante de cada provedor.

    O failover exige fabricantes distintos: dois provedores da mesma empresa
    caem juntos, e o fallback vira enfeite (D-011).
    """
    return {
        "gemini": "google",
        "anthropic": "anthropic",
        "openai": "openai",
        "rule_based": "local",
    }[provider]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Usado por testes que manipulam variaveis de ambiente."""
    get_settings.cache_clear()
