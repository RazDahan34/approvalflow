"""Notification service (Engine).

Delivers final results to submitters over Server-Sent Events (M8/F2): subscribes to
`invoice.status-changed` and pushes each transition to any browser listening on
GET /events/{trackingId}. Delivery is a live channel only — the durable status
projection is owned by intake, so nothing here needs persistence.
"""

import asyncio
import json

from approvalflow_common import create_app, get_logger, set_correlation_id
from approvalflow_common.topics import INVOICE_STATUS_CHANGED
from dapr.ext.fastapi import DaprApp
from fastapi import Request
from fastapi.responses import StreamingResponse

SERVICE = "notification"
TERMINAL = {"paid", "payment_failed", "rejected", "duplicate"}

app = create_app(SERVICE)
dapr_app = DaprApp(app)
log = get_logger(SERVICE)

# trackingId -> last seen event (replayed to late subscribers)
_latest: dict[str, dict] = {}
# trackingId -> live listener queues
_listeners: dict[str, set[asyncio.Queue]] = {}


@dapr_app.subscribe(pubsub="pubsub", topic=INVOICE_STATUS_CHANGED)
async def on_status_changed(request: Request) -> dict:
    envelope = await request.json()
    data = envelope.get("data", envelope)
    set_correlation_id(data.get("correlationId", ""))
    tracking_id = data.get("trackingId", "")

    _latest[tracking_id] = data
    delivered = 0
    for queue in _listeners.get(tracking_id, set()).copy():
        queue.put_nowait(data)
        delivered += 1

    log.info(
        "status pushed",
        extra={
            "event": "notified",
            "trackingId": tracking_id,
            "status": data.get("status"),
            "listeners": delivered,
        },
    )
    return {"success": True}


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@app.get("/events/{tracking_id}")
async def events(tracking_id: str) -> StreamingResponse:
    """SSE stream of status transitions; closes itself after a terminal status."""
    queue: asyncio.Queue = asyncio.Queue()
    _listeners.setdefault(tracking_id, set()).add(queue)

    async def stream():
        try:
            last = _latest.get(tracking_id)
            if last:
                yield _sse(last)
                if last.get("status") in TERMINAL:
                    return
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=20)
                except TimeoutError:
                    yield ": keepalive\n\n"  # comment frame keeps proxies from closing us
                    continue
                yield _sse(item)
                if item.get("status") in TERMINAL:
                    return
        finally:
            _listeners.get(tracking_id, set()).discard(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
