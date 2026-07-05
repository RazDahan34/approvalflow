"""Unit tests for the transactional outbox (N3) — provable offline via the
InMemory backend that honors the exact CAS contract of the Dapr store."""

from approvalflow_common.outbox import INDEX_KEY, Outbox
from approvalflow_common.state import InMemoryStateBackend


class RecordingPublisher:
    def __init__(self, fail_times: int = 0):
        self.fail_times = fail_times
        self.published: list[tuple[str, dict]] = []

    def __call__(self, topic: str, payload: dict) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("broker down")
        self.published.append((topic, payload))


def make_outbox(fail_times: int = 0):
    backend = InMemoryStateBackend()
    publisher = RecordingPublisher(fail_times)
    return Outbox(backend, publisher), backend, publisher


def test_stage_then_flush_publishes_and_clears_index():
    outbox, backend, publisher = make_outbox()
    outbox.stage("m1", "invoice.submitted", {"trackingId": "m1"})
    assert outbox.flush_one("m1") is True
    assert publisher.published == [("invoice.submitted", {"trackingId": "m1"})]
    index, _ = backend.get(INDEX_KEY)
    assert index["ids"] == []


def test_failed_publish_stays_pending_and_sweep_retries():
    outbox, backend, publisher = make_outbox(fail_times=1)
    outbox.stage("m1", "invoice.submitted", {"trackingId": "m1"})
    assert outbox.flush_one("m1") is False  # broker down -> stays staged
    index, _ = backend.get(INDEX_KEY)
    assert index["ids"] == ["m1"]

    assert outbox.sweep() == 1  # relay catches up when the broker is back
    assert publisher.published == [("invoice.submitted", {"trackingId": "m1"})]
    index, _ = backend.get(INDEX_KEY)
    assert index["ids"] == []


def test_flush_is_idempotent_after_publish():
    outbox, _, publisher = make_outbox()
    outbox.stage("m1", "t", {"x": 1})
    outbox.flush_one("m1")
    # A racing relay / retried request flushes again: no second publish.
    assert outbox.flush_one("m1") is True
    assert len(publisher.published) == 1


def test_staging_twice_is_a_noop():
    outbox, backend, _ = make_outbox()
    outbox.stage("m1", "t", {"x": 1})
    outbox.stage("m1", "t", {"x": 999})  # half-written retry heals, no overwrite
    record, _ = backend.get("outbox:m1")
    assert record["payload"] == {"x": 1}
    index, _ = backend.get(INDEX_KEY)
    assert index["ids"] == ["m1"]


def test_sweep_handles_multiple_pending():
    outbox, _, publisher = make_outbox(fail_times=2)
    outbox.stage("m1", "t", {"n": 1})
    outbox.stage("m2", "t", {"n": 2})
    outbox.flush_one("m1")  # fails
    outbox.flush_one("m2")  # fails
    assert outbox.sweep() == 2
    assert {p[1]["n"] for p in publisher.published} == {1, 2}
