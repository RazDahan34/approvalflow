"""State backends with optimistic concurrency (ETag CAS).

`DaprStateBackend` is the real thing; `InMemoryStateBackend` implements the exact same
contract for unit tests, so concurrency logic (budget reservations, idempotency
records) is provable offline.
"""

import json
from typing import Protocol

from dapr.clients import DaprClient
from dapr.clients.grpc._state import Concurrency, StateOptions

from .logging import get_logger

log = get_logger("state")


class StateBackend(Protocol):
    def get(self, key: str) -> tuple[dict | None, str]:
        """Return (value, etag); (None, "") when the key does not exist."""
        ...

    def try_create(self, key: str, value: dict) -> bool:
        """Insert only if absent. False when the key already exists."""
        ...

    def save_cas(self, key: str, value: dict, etag: str) -> bool:
        """Compare-and-swap on the etag. False on a concurrent modification."""
        ...


class DaprStateBackend:
    def __init__(self, store_name: str) -> None:
        self.store_name = store_name

    def get(self, key: str) -> tuple[dict | None, str]:
        with DaprClient() as client:
            result = client.get_state(self.store_name, key)
            if not result.data:
                return None, ""
            return json.loads(result.data), result.etag or ""

    def try_create(self, key: str, value: dict) -> bool:
        # An empty etag + first-write concurrency == "insert only if absent".
        return self._save(key, value, etag="")

    def save_cas(self, key: str, value: dict, etag: str) -> bool:
        return self._save(key, value, etag=etag)

    def _save(self, key: str, value: dict, etag: str) -> bool:
        try:
            with DaprClient() as client:
                client.save_state(
                    self.store_name,
                    key,
                    json.dumps(value, default=str),
                    etag=etag,
                    options=StateOptions(concurrency=Concurrency.first_write),
                )
            return True
        except Exception as exc:
            message = str(exc).lower()
            if "unavailable" in message or "connect" in message:
                # Infrastructure problem — never masquerade as a CAS conflict (M15).
                raise
            # etag mismatch / key exists -> normal contention, the caller re-reads.
            log.info("state save rejected", extra={"key": key, "reason": str(exc)[:120]})
            return False


class InMemoryStateBackend:
    """Test double with real CAS semantics (etag = version counter)."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[dict, int]] = {}

    def get(self, key: str) -> tuple[dict | None, str]:
        if key not in self._data:
            return None, ""
        value, version = self._data[key]
        return json.loads(json.dumps(value)), str(version)

    def try_create(self, key: str, value: dict) -> bool:
        if key in self._data:
            return False
        self._data[key] = (value, 1)
        return True

    def save_cas(self, key: str, value: dict, etag: str) -> bool:
        if key not in self._data:
            return False
        _, version = self._data[key]
        if str(version) != etag:
            return False
        self._data[key] = (value, version + 1)
        return True
