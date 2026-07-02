"""Unit tests for environment-driven configuration."""

from approvalflow_common.config import Settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.autonomy_ceiling_usd == 500.0
    assert s.autonomy_confidence == 0.80
    assert s.pubsub_name == "pubsub"
    assert s.statestore_name == "statestore"


def test_env_override(monkeypatch):
    monkeypatch.setenv("AUTONOMY_CEILING_USD", "500")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    s = Settings(_env_file=None)
    assert s.autonomy_ceiling_usd == 500.0
    assert s.llm_provider == "groq"
