"""Structured logging, request IDs, Prometheus metrics and OpenTelemetry tracing."""

from __future__ import annotations

import logging
import os
import re
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import IO

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from app.config import settings

SERVICE = settings.app_name
REQUEST_ID_HEADER = "X-Request-ID"
PROBE_PATHS = frozenset({"/health", "/metrics"})
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

logger = structlog.get_logger("billing.http")

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests served, by route template and status.",
    ["service", "method", "handler", "status"],
)
HTTP_REQUEST_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency, by route template.",
    ["service", "method", "handler"],
)
PLAN_CHANGES = Counter(
    "billing_plan_changes_total",
    "Plan change requests by outcome (created, conflict, invalid).",
    ["outcome"],
)
PLAN_CHANGE_SECONDS = Histogram(
    "billing_plan_change_duration_seconds",
    "Time to apply a plan change, by outcome.",
    ["outcome"],
)
ENTITLEMENT_LOOKUPS = Counter(
    "billing_entitlement_lookups_total",
    "Tenant entitlement lookups by outcome (found, not_found).",
    ["outcome"],
)


def _add_service(_logger: object, _method: str, event_dict: dict) -> dict:
    event_dict.setdefault("service", SERVICE)
    return event_dict


def _add_trace_context(_logger: object, _method: str, event_dict: dict) -> dict:
    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        event_dict["trace_id"] = format(context.trace_id, "032x")
        event_dict["span_id"] = format(context.span_id, "016x")
    return event_dict


def configure_logging(log_format: str = "json", stream: IO[str] | None = None) -> None:
    """Route structlog and stdlib (uvicorn, psycopg) logs through one JSON or text renderer."""
    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_service,
        _add_trace_context,
    ]
    renderer = (
        structlog.dev.ConsoleRenderer(colors=False)
        if log_format == "text"
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    # The request middleware below writes the access log with request/trace IDs.
    logging.getLogger("uvicorn.access").disabled = True


def configure_tracing(app: FastAPI, enabled: bool) -> None:
    """Server spans for FastAPI and client spans for psycopg; exported over OTLP/HTTP when an
    `OTEL_EXPORTER_OTLP_*ENDPOINT` is configured, otherwise kept local for log correlation."""
    if not enabled:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE}))
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(
        app, excluded_urls="health,metrics", tracer_provider=provider
    )
    instrumentor = PsycopgInstrumentor()
    if not instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.instrument(tracer_provider=provider, skip_dep_check=True)


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", "unmatched")


def install_request_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = (
            incoming if incoming and _VALID_REQUEST_ID.match(incoming) else str(uuid.uuid4())
        )
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        trace.get_current_span().set_attribute("request.id", request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled_exception", method=request.method, path=request.url.path)
            response = JSONResponse(
                status_code=500,
                content={"detail": "Internal server error", "request_id": request_id},
            )
        elapsed = time.perf_counter() - start
        handler = _route_template(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        HTTP_REQUESTS.labels(SERVICE, request.method, handler, str(response.status_code)).inc()
        HTTP_REQUEST_SECONDS.labels(SERVICE, request.method, handler).observe(elapsed)
        log = logger.debug if request.url.path in PROBE_PATHS else logger.info
        log(
            "http_request",
            method=request.method,
            path=request.url.path,
            handler=handler,
            status=response.status_code,
            duration_ms=round(elapsed * 1000, 2),
        )
        structlog.contextvars.clear_contextvars()
        return response


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
