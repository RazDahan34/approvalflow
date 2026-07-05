"""Unit tests for JWT issue/verify (N1)."""

import pytest
from approvalflow_common.security import AuthError, issue_token, verify_token


def test_round_trip_carries_subject_and_role():
    token = issue_token("dana.cohen", "submitter")
    claims = verify_token(token)
    assert claims["sub"] == "dana.cohen"
    assert claims["role"] == "submitter"


def test_unknown_role_cannot_be_issued():
    with pytest.raises(ValueError):
        issue_token("mallory", "superuser")


def test_expired_token_is_rejected():
    token = issue_token("bob", "approver", ttl_seconds=-10)
    with pytest.raises(AuthError, match="invalid token"):
        verify_token(token)


def test_tampered_token_is_rejected():
    token = issue_token("alice", "approver")
    header, payload, signature = token.split(".")
    with pytest.raises(AuthError):
        verify_token(f"{header}.{payload}.AAAA{signature[4:]}")


def test_garbage_is_rejected():
    with pytest.raises(AuthError):
        verify_token("not-a-jwt-at-all")
