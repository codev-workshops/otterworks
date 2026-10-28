"""Tracing, request-ID propagation and access logging for the Search Service."""

from __future__ import annotations

import os
import re
import time
import uuid
from typing import Any

import structlog
from flask import Flask, Response, g, jsonify, request
from opentelemetry import trace
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from werkzeug.exceptions import HTTPException

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_PROBE_PATHS = frozenset({"/health", "/health/ready", "/metrics"})

logger = structlog.get_logger("search.http")


def resolve_request_id(candidate: str | None) -> str:
    """Reuse a well-formed inbound request ID, otherwise mint a UUID."""
    if candidate and _VALID_REQUEST_ID.fullmatch(candidate):
        return candidate
    return str(uuid.uuid4())


def add_trace_context(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor: attach the active trace/span IDs to every log line."""
    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        event_dict.setdefault("trace_id", format(span_context.trace_id, "032x"))
        event_dict.setdefault("span_id", format(span_context.span_id, "016x"))
    return event_dict


def _install_tracer_provider(service_name: str) -> None:
    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)


def configure_tracing(app: Flask, service_name: str, enabled: bool) -> None:
    """Server spans for Flask plus client spans (and traceparent) for MeiliSearch calls."""
    if not enabled:
        return
    _install_tracer_provider(service_name)
    FlaskInstrumentor().instrument_app(app, excluded_urls="health,metrics")
    requests_instrumentor = RequestsInstrumentor()
    if not requests_instrumentor.is_instrumented_by_opentelemetry:
        requests_instrumentor.instrument()


def install_request_context(app: Flask) -> None:
    """Request-ID handling, access logging and a JSON 500 for unhandled errors."""

    @app.before_request
    def _bind_request_id() -> None:
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        g.request_id = request_id
        g.request_started = time.monotonic()
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        trace.get_current_span().set_attribute("request.id", request_id)

    @app.after_request
    def _log_request(response: Response) -> Response:
        request_id = g.get("request_id") or resolve_request_id(None)
        response.headers[REQUEST_ID_HEADER] = request_id
        elapsed_ms = (time.monotonic() - g.get("request_started", time.monotonic())) * 1000
        log = logger.debug if request.path in _PROBE_PATHS else logger.info
        log(
            "http_request",
            method=request.method,
            path=request.path,
            route=request.url_rule.rule if request.url_rule else "unmatched",
            status=response.status_code,
            duration_ms=round(elapsed_ms, 2),
        )
        return response

    @app.teardown_request
    def _clear_request_id(_exc: BaseException | None) -> None:
        structlog.contextvars.clear_contextvars()

    @app.errorhandler(Exception)
    def _handle_unexpected(error: Exception) -> Any:
        if isinstance(error, HTTPException):
            return error
        logger.exception("unhandled_request_error", method=request.method, path=request.path)
        return jsonify({"error": "Internal server error", "request_id": g.get("request_id")}), 500
