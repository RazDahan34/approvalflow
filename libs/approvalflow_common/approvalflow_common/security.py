"""Self-signed JWT authentication with roles (N1).

Mechanism only — token issuing and verification. The gateway decides which role may
call which route. HS256 with a shared secret is deliberate: the brief allows a
self-signed token, and swapping to a real IdP later means changing the issuer, not
the services.
"""

import time

import jwt

from .config import get_settings

ROLES = ("submitter", "approver", "admin")
DEFAULT_TTL_SECONDS = 8 * 3600


class AuthError(Exception):
    """Invalid, expired or missing credentials (maps to HTTP 401)."""


def issue_token(subject: str, role: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    if role not in ROLES:
        raise ValueError(f"unknown role '{role}' (expected one of {ROLES})")
    settings = get_settings()
    now = int(time.time())
    return jwt.encode(
        {
            "sub": subject,
            "role": role,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "iat": now,
            "exp": now + ttl_seconds,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def verify_token(token: str) -> dict:
    """Return the claims or raise AuthError. Signature, expiry, issuer and audience
    are all enforced — a token that fails ANY check is worthless."""
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"invalid token: {exc}") from exc
    if claims.get("role") not in ROLES:
        raise AuthError("token carries no valid role")
    return claims
