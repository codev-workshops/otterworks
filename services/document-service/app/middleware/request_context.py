"""Request-ID propagation, trace-correlated logging and a JSON 500 for unhandled errors."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry import trace

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
PROBE_PATHS = frozenset({"/health", "/metrics", "/ready"})

logger = structlog.get_logger("document.http")


def resolve_request_id(candidate: str | None) -> str:
    """Reuse a well-formed inbound request ID, otherwise mint a UUID."""
    if candidate and _VALID_REQUEST_ID.fullmatch(candidate):
        return candidate
    return str(uuid.uuid4())


def add_service_and_trace(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor: tag every line with the service and the active trace/span IDs."""
    event_dict.setdefault("service", "document-service")
    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        event_dict.setdefault("trace_id", format(span_context.trace_id, "032x"))
        event_dict.setdefault("span_id", format(span_context.span_id, "016x"))
    return event_dict


def _route_label(request: Request) -> str:
    route = request.scope.get("route")
    return str(getattr(route, "path", None) or "unmatched")


def install(app: FastAPI) -> None:
    """Install inside the OpenTelemetry middleware so the server span is already active."""

    @app.middleware("http")
    async def _request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        trace.get_current_span().set_attribute("request.id", request_id)
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "unhandled_request_error", method=request.method, path=request.url.path
            )
            response = JSONResponse(
                {"error": "Internal server error", "request_id": request_id}, status_code=500
            )
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers[REQUEST_ID_HEADER] = request_id
        if request.url.path not in PROBE_PATHS:
            logger.info(
                "http_request",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                route=_route_label(request),
                status=response.status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        return response
