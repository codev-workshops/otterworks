"""OpenTelemetry tracing for search-service (off unless OTEL_SDK_DISABLED=false)."""

from __future__ import annotations

import os
from typing import Any

from flask import Flask
from opentelemetry import trace


def tracing_enabled() -> bool:
    return os.getenv("OTEL_SDK_DISABLED", "true").strip().lower() == "false"


def configure_tracing(app: Flask) -> bool:
    """Instrument Flask and export spans over OTLP/HTTP to OTEL_EXPORTER_OTLP_ENDPOINT."""
    if not tracing_enabled():
        return False

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.flask import FlaskInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": os.getenv("OTEL_SERVICE_NAME", "search-service")}
        )
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FlaskInstrumentor().instrument_app(app, excluded_urls="health,metrics")
    return True


def add_trace_context(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor: add trace_id/span_id of the active span, if any."""
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict
