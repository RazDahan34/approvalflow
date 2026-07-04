"""Unit tests for the live threshold source (M13/F7)."""

from approvalflow_common.policy_source import PolicySource
from approvalflow_common.schemas import Category


def test_store_values_override_defaults():
    source = PolicySource(
        ttl_seconds=999,
        fetch=lambda: {
            "autonomy.ceiling_usd": "900",
            "autonomy.confidence": "0.9",
            "autonomy.tier.other": "50",
        },
    )
    config = source.current()
    assert config.ceiling_usd == 900.0
    assert config.confidence_threshold == 0.9
    assert config.category_ceilings[Category.other] == 50.0
    # Untouched tiers keep their defaults (travel stays at Raz's 500).
    assert config.category_ceilings[Category.travel] == 500.0


def test_missing_keys_keep_defaults():
    config = PolicySource(ttl_seconds=999, fetch=dict).current()
    assert config.ceiling_usd == 500.0
    assert config.confidence_threshold == 0.80
    assert config.category_ceilings[Category.saas] == 200.0


def test_store_failure_falls_back_to_env_defaults():
    def broken():
        raise ConnectionError("config store down")

    config = PolicySource(ttl_seconds=999, fetch=broken).current()
    # Fail-safe: the shipped posture, never a wide-open one.
    assert config.ceiling_usd == 500.0
    assert config.category_ceilings[Category.other] == 100.0


def test_ttl_caches_and_expires():
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return {"autonomy.ceiling_usd": "500"}

    source = PolicySource(ttl_seconds=999, fetch=counting)
    source.current()
    source.current()
    assert calls["n"] == 1  # cached within TTL

    expired = PolicySource(ttl_seconds=0, fetch=counting)
    expired.current()
    expired.current()
    assert calls["n"] == 3  # ttl=0 -> refetch each time


def test_malformed_value_keeps_current_posture():
    # A controller's typo must not take decisions down OR silently widen autonomy.
    source = PolicySource(ttl_seconds=999, fetch=lambda: {"autonomy.ceiling_usd": "not-a-number"})
    config = source.current()  # no crash
    assert config.ceiling_usd == 500.0  # env default on first load


def test_last_known_good_beats_env_defaults():
    # Store said 200 (tighter than the 500 default), then broke: we must KEEP 200 —
    # falling back to the wider default would silently widen autonomy.
    responses = iter([{"autonomy.ceiling_usd": "200"}, {"autonomy.ceiling_usd": "oops"}])
    source = PolicySource(ttl_seconds=0, fetch=lambda: next(responses))
    assert source.current().ceiling_usd == 200.0
    assert source.current().ceiling_usd == 200.0  # malformed refresh -> last known good