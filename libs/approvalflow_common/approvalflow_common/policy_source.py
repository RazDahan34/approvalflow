"""Live autonomy thresholds from the Dapr configuration store (M13/F7).

A controller changes a value in the store (one `redis-cli SET`, no redeploy) and
decisions pick it up within the cache TTL. On any store failure we fall back LOUDLY
to the env-configured defaults — a broken config store must never widen autonomy
silently (M15).
"""

import time

import httpx

from .config import get_settings
from .decision import PolicyConfig
from .logging import get_logger
from .schemas import Category

log = get_logger("policy-source")

CONFIG_KEYS = [
    "autonomy.ceiling_usd",
    "autonomy.confidence",
    *(f"autonomy.tier.{category.value}" for category in Category),
]


class PolicySource:
    """Fetch-with-TTL-cache over the Dapr Configuration API."""

    def __init__(self, ttl_seconds: float | None = None, fetch=None) -> None:
        settings = get_settings()
        self.ttl = settings.policy_cache_ttl_seconds if ttl_seconds is None else ttl_seconds
        self._fetch = fetch or self._fetch_from_dapr
        self._cached: PolicyConfig | None = None
        self._expires = 0.0

    def current(self) -> PolicyConfig:
        now = time.monotonic()
        if self._cached is None or now >= self._expires:
            self._cached = self._load()
            self._expires = now + self.ttl
        return self._cached

    # ── internals ──

    def _defaults(self) -> PolicyConfig:
        settings = get_settings()
        return PolicyConfig(
            ceiling_usd=settings.autonomy_ceiling_usd,
            confidence_threshold=settings.autonomy_confidence,
        )

    def _load(self) -> PolicyConfig:
        # On ANY problem we keep the last-known-good posture (or env defaults on first
        # load). Never fall "open": a store outage or a controller's typo must not
        # widen autonomy or take decisions down.
        fallback = self._cached or self._defaults()
        try:
            values = self._fetch()
        except Exception as exc:
            log.error(
                "config store unavailable — keeping current posture",
                extra={"error": str(exc)[:150]},
            )
            return fallback
        if not values:
            log.warning("config store empty — keeping current posture")
            return fallback

        try:
            tiers = dict(fallback.category_ceilings)
            for category in Category:
                key = f"autonomy.tier.{category.value}"
                if key in values:
                    tiers[category] = float(values[key])
            config = PolicyConfig(
                ceiling_usd=float(values.get("autonomy.ceiling_usd", fallback.ceiling_usd)),
                confidence_threshold=float(values.get("autonomy.confidence", fallback.confidence_threshold)),
                category_ceilings=tiers,
            )
        except (TypeError, ValueError) as exc:
            log.error(
                "malformed config value — keeping current posture",
                extra={"error": str(exc)[:150], "values": values},
            )
            return fallback
        log.info(
            "policy thresholds loaded",
            extra={
                "ceiling": config.ceiling_usd,
                "confidence": config.confidence_threshold,
                "tiers": {c.value: v for c, v in config.category_ceilings.items()},
            },
        )
        return config

    def _fetch_from_dapr(self) -> dict[str, str]:
        settings = get_settings()
        url = f"http://localhost:{settings.dapr_http_port}/v1.0/configuration/{settings.config_store_name}"
        with httpx.Client(timeout=3) as client:
            response = client.get(url, params=[("key", key) for key in CONFIG_KEYS])
            response.raise_for_status()
            body = response.json() or {}
            return {
                key: item["value"]
                for key, item in body.items()
                if isinstance(item, dict) and item.get("value") is not None
            }
