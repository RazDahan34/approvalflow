"""Thin helpers over the Dapr building blocks (pub/sub + state).

Keeping these here means services never touch the raw SDK directly and the
correlation id is always propagated on published messages.
"""

import json

import httpx
from dapr.clients import DaprClient

from .config import get_settings
from .correlation import CORRELATION_ID_HEADER, TRACEPARENT_HEADER, get_correlation_id, get_traceparent


def publish_event(topic: str, data: dict, pubsub_name: str | None = None) -> None:
    settings = get_settings()
    # Carrying the W3C trace context on the CloudEvent keeps the distributed trace in
    # one piece across the async hop (N4).
    metadata = {"correlationId": get_correlation_id()}
    if get_traceparent():
        metadata["cloudevent.traceparent"] = get_traceparent()
    with DaprClient() as client:
        client.publish_event(
            pubsub_name=pubsub_name or settings.pubsub_name,
            topic_name=topic,
            data=json.dumps(data, default=str),
            data_content_type="application/json",
            publish_metadata=metadata,
        )


def save_state(key: str, value: dict, store_name: str | None = None) -> None:
    settings = get_settings()
    with DaprClient() as client:
        client.save_state(
            store_name or settings.statestore_name,
            key,
            json.dumps(value, default=str),
        )


def get_state(key: str, store_name: str | None = None) -> dict | None:
    settings = get_settings()
    with DaprClient() as client:
        result = client.get_state(store_name or settings.statestore_name, key)
        if result.data:
            return json.loads(result.data)
        return None


def invoke_service(
    app_id: str,
    method: str,
    http_verb: str = "GET",
    json_body: dict | None = None,
    timeout: float = 15.0,
) -> httpx.Response:
    """Synchronous Dapr service invocation via the local sidecar (M5).

    Routes through http://localhost:<dapr-http-port>/v1.0/invoke/<app>/method/<method>,
    so Dapr handles discovery, mTLS and retries. The correlation id is propagated.
    """
    settings = get_settings()
    url = f"http://localhost:{settings.dapr_http_port}/v1.0/invoke/{app_id}/method/{method}"
    headers = {CORRELATION_ID_HEADER: get_correlation_id()}
    if get_traceparent():
        headers[TRACEPARENT_HEADER] = get_traceparent()
    with httpx.Client(timeout=timeout) as client:
        return client.request(http_verb, url, json=json_body, headers=headers)
