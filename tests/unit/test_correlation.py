"""Unit tests for correlation-id handling."""

from approvalflow_common.correlation import (
    ensure_correlation_id,
    get_correlation_id,
    new_correlation_id,
    set_correlation_id,
)


def test_new_id_is_unique():
    assert new_correlation_id() != new_correlation_id()


def test_set_and_get():
    set_correlation_id("abc123")
    assert get_correlation_id() == "abc123"


def test_ensure_uses_incoming_when_present():
    assert ensure_correlation_id("given-id") == "given-id"
    assert get_correlation_id() == "given-id"


def test_ensure_mints_when_absent():
    minted = ensure_correlation_id(None)
    assert minted
    assert get_correlation_id() == minted
