"""Exactly-once projection helpers (audit dedup on pub/sub redelivery)."""

from approvalflow_common.projections import SEEN_CAP, append_once, apply_once


def increment(doc: dict) -> dict:
    doc["count"] = doc.get("count", 0) + 1
    return doc


def test_redelivered_event_counts_once():
    doc: dict = {}
    apply_once(doc, "evt-1", increment)
    apply_once(doc, "evt-1", increment)  # redelivery
    apply_once(doc, "evt-2", increment)
    assert doc["count"] == 2


def test_missing_event_id_still_applies():
    # No id -> no dedup possible; better to count than to drop.
    doc: dict = {}
    apply_once(doc, "", increment)
    apply_once(doc, "", increment)
    assert doc["count"] == 2


def test_seen_list_is_bounded():
    doc: dict = {}
    for i in range(SEEN_CAP + 100):
        apply_once(doc, f"evt-{i}", increment)
    assert len(doc["_seen"]) == SEEN_CAP
    assert doc["count"] == SEEN_CAP + 100


def test_trail_append_dedups_by_event_id():
    trail: dict = {}
    append_once(trail, "evt-1", {"type": "submitted"})
    append_once(trail, "evt-1", {"type": "submitted"})  # redelivery
    append_once(trail, "evt-2", {"type": "decided"})
    assert [e["type"] for e in trail["events"]] == ["submitted", "decided"]
    assert trail["events"][0]["eventId"] == "evt-1"
