"""Exactly-once helpers for event-driven projections (F8/F9 + M10).

Pub/sub is at-least-once: a redelivered event must not double-count metrics or
duplicate trail entries. Each projection document embeds a bounded list of processed
CloudEvent ids; the check and the mutation land in the SAME document, so the CAS
write that persists the counter also persists the dedup mark — atomically.
"""

from collections.abc import Callable

SEEN_KEY = "_seen"
SEEN_CAP = 500  # bounded memory; far wider than any realistic redelivery window


def apply_once(document: dict, event_id: str, mutate: Callable[[dict], dict]) -> dict:
    """Apply `mutate` unless this event id was already applied to this document."""
    seen = document.setdefault(SEEN_KEY, [])
    if event_id and event_id in seen:
        return document
    if event_id:
        seen.append(event_id)
        del seen[:-SEEN_CAP]
    return mutate(document)


def append_once(trail: dict, event_id: str, entry: dict) -> dict:
    """Append a trail entry unless an entry with this event id already exists."""
    events = trail.setdefault("events", [])
    if event_id and any(e.get("eventId") == event_id for e in events):
        return trail
    if event_id:
        entry = {**entry, "eventId": event_id}
    events.append(entry)
    return trail
