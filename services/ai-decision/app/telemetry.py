"""OpenTelemetry wiring for the AI service (N4).

The Dapr sidecars already trace every invocation and pub/sub hop into Zipkin; this
adds the *application-level* spans inside ai-decision — the LLM chat calls and the
MCP tool calls — joined to the same end-to-end trace via the incoming `traceparent`.
"""

import os

from approvalflow_common.logging import get_logger

log = get_logger("telemetry")


def setup_tracing(app) -> None:
    if os.getenv("OTEL_ENABLED", "true").lower() not in ("1", "true", "yes"):
        log.info("otel disabled by OTEL_ENABLED")
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.zipkin.json import ZipkinExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        endpoint = os.getenv("ZIPKIN_ENDPOINT", "http://zipkin:9411/api/v2/spans")
        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: "ai-decision-app"}))
        provider.add_span_processor(BatchSpanProcessor(ZipkinExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)

        FastAPIInstrumentor.instrument_app(app)  # joins the sidecar's traceparent
        HTTPXClientInstrumentor().instrument()  # LLM + MCP calls become child spans
        log.info("otel tracing up", extra={"exporter": endpoint})
    except Exception as exc:  # observability must never take the service down
        log.error("otel setup failed; continuing without app spans", extra={"error": str(exc)[:200]})


def get_tracer():
    from opentelemetry import trace

    return trace.get_tracer("approvalflow.ai-decision")
