"""Centralised, environment-driven configuration.

Every value can be overridden by an environment variable (Dapr/compose injects them),
so nothing is hard-coded and thresholds are changeable without a code change (M13/F7).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── Service identity ──
    service_name: str = "approvalflow"
    log_level: str = "INFO"

    # ── Dapr component names (must match the YAML in dapr/components) ──
    pubsub_name: str = "pubsub"
    statestore_name: str = "statestore"
    secretstore_name: str = "secretstore"
    dapr_http_port: int = 3500

    # ── LLM provider — swappable by configuration (M15) ──
    llm_provider: str = "stub"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_timeout_seconds: int = 30
    llm_max_retries: int = 2

    # ── Policy document (RAG source, N5) ──
    policy_path: str = "policy.md"

    # ── MCP tool server (B2); empty = agent runs without remote tools ──
    mcp_server_url: str = ""

    # ── Live thresholds via the Dapr configuration store (M13/F7) ──
    config_store_name: str = "configstore"
    policy_cache_ttl_seconds: float = 10.0

    # ── Autonomy thresholds (the dilemma) — externally configurable (M13/F7).
    #    The envelope; per-category tiers live in PolicyConfig / Dapr config. ──
    autonomy_ceiling_usd: float = 500.0
    autonomy_confidence: float = 0.80

    # ── Gateway (M6) ──
    gateway_rate_limit: str = "100/minute"

    # ── Security (N1) ──
    # Dev-only placeholder, intentionally >=32 bytes (HS256 minimum); override in .env.
    jwt_secret: str = "change-me-dev-only-secret-0123456789abcdef"
    jwt_issuer: str = "approvalflow"
    jwt_audience: str = "approvalflow"


@lru_cache
def get_settings() -> Settings:
    """Cached singleton so every module sees the same configuration."""
    return Settings()
