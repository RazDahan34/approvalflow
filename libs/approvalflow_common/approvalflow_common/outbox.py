"""Transactional-outbox pattern (N3).

The submission record and the intent-to-publish land in the SAME state store before
the caller gets a 202; the actual publish is a fast-path attempt plus a background
sweeper for anything that failed. Combined with idempotent consumers downstream
(workflow scheduling is keyed by tracking id), delivery is effectively exactly-once.

Dapr also offers a built-in outbox on the state component; this explicit
implementation was chosen so the pattern itself is visible and unit-testable
(see docs/adr/0009 note).
"""

from collections.abc import Callable

from .logging import get_logger
from .state import StateBackend

log = get_logger("outbox")

INDEX_KEY = "outbox:index"
MAX_CAS_RETRIES = 8


class Outbox:
    def __init__(self, backend: StateBackend, publish: Callable[[str, dict], None]) -> None:
        self.backend = backend
        self.publish = publish

    # ── write side ──

    def stage(self, message_id: str, topic: str, payload: dict) -> None:
        """Record the intent to publish. Heals a half-written retry: staging the same
        id again is a no-op thanks to create-only semantics."""
        self.backend.try_create(
            f"outbox:{message_id}", {"topic": topic, "payload": payload, "state": "pending"}
        )
        self._index_update(add=message_id)

    def flush_one(self, message_id: str) -> bool:
        """Try to publish one staged message; True when it is out the door."""
        record, etag = self.backend.get(f"outbox:{message_id}")
        if record is None:
            self._index_update(remove=message_id)
            return True
        if record.get("state") == "published":
            self._index_update(remove=message_id)
            return True
        try:
            self.publish(record["topic"], record["payload"])
        except Exception as exc:
            log.warning(
                "outbox publish failed; will retry on the next sweep",
                extra={"messageId": message_id, "error": str(exc)[:120]},
            )
            return False
        record["state"] = "published"
        self.backend.save_cas(f"outbox:{message_id}", record, etag)
        self._index_update(remove=message_id)
        return True

    # ── relay side ──

    def sweep(self) -> int:
        """Publish everything still pending; returns how many went out."""
        index, _ = self.backend.get(INDEX_KEY)
        pending = list((index or {}).get("ids", []))
        return sum(1 for message_id in pending if self.flush_one(message_id))

    # ── internals ──

    def _index_update(self, add: str | None = None, remove: str | None = None) -> None:
        for _ in range(MAX_CAS_RETRIES):
            current, etag = self.backend.get(INDEX_KEY)
            if current is None:
                if add is None or self.backend.try_create(INDEX_KEY, {"ids": [add]}):
                    return
                continue
            ids = list(dict.fromkeys(current.get("ids", [])))
            if add and add not in ids:
                ids.append(add)
            if remove and remove in ids:
                ids.remove(remove)
            if ids == current.get("ids", []):
                return
            if self.backend.save_cas(INDEX_KEY, {"ids": ids}, etag):
                return
        log.error("outbox index update lost after retries", extra={"add": add, "remove": remove})
