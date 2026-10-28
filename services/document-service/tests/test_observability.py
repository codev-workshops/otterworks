"""Request IDs, trace-correlated logs, dependency-backed /health, JSON 500s and domain metrics."""

import re
import uuid
from collections.abc import AsyncGenerator

import pytest
import structlog
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import REGISTRY
from sqlalchemy.exc import OperationalError

from app.db.session import get_db
from app.main import app
from app.middleware.request_context import REQUEST_ID_HEADER, add_service_and_trace

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@app.get("/_test/context")
async def _context() -> dict:
    return structlog.contextvars.get_contextvars()


@app.get("/_test/boom")
async def _boom() -> dict:
    raise RuntimeError("db password=hunter2")


@pytest.mark.asyncio
async def test_valid_request_id_is_echoed_and_bound_to_logs(client: AsyncClient):
    resp = await client.get("/_test/context", headers={REQUEST_ID_HEADER: "req-abc.1:2"})
    assert resp.headers[REQUEST_ID_HEADER] == "req-abc.1:2"
    assert resp.json()["request_id"] == "req-abc.1:2"


@pytest.mark.asyncio
async def test_missing_or_invalid_request_id_is_replaced(client: AsyncClient):
    assert UUID_RE.match((await client.get("/health")).headers[REQUEST_ID_HEADER])
    bad = await client.get("/health", headers={REQUEST_ID_HEADER: "no spaces <allowed>"})
    assert UUID_RE.match(bad.headers[REQUEST_ID_HEADER])


@pytest.mark.asyncio
async def test_unhandled_error_returns_json_500_without_details(client: AsyncClient):
    resp = await client.get("/_test/boom", headers={REQUEST_ID_HEADER: "req-500"})
    assert resp.status_code == 500
    assert resp.json() == {"error": "Internal server error", "request_id": "req-500"}
    assert "hunter2" not in resp.text
    assert resp.headers[REQUEST_ID_HEADER] == "req-500"


@pytest.mark.asyncio
async def test_unknown_route_keeps_default_404(client: AsyncClient):
    assert (await client.get("/does-not-exist")).status_code == 404


class _BrokenSession:
    async def execute(self, *_args, **_kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))


@pytest.mark.asyncio
async def test_health_returns_503_when_database_is_down():
    async def _broken_db() -> AsyncGenerator[_BrokenSession, None]:
        yield _BrokenSession()

    app.dependency_overrides[get_db] = _broken_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/health")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"
    assert resp.json()["checks"]["database"] == "disconnected"


def test_log_processor_adds_service_and_active_trace_ids():
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("op") as span:
        event = add_service_and_trace(None, "info", {"event": "x"})
    ctx = span.get_span_context()
    assert event["service"] == "document-service"
    assert event["trace_id"] == format(ctx.trace_id, "032x")
    assert event["span_id"] == format(ctx.span_id, "016x")
    assert "trace_id" not in add_service_and_trace(None, "info", {"event": "y"})


def _ops(operation: str, outcome: str) -> float:
    labels = {"service": "document-service", "operation": operation, "outcome": outcome}
    return REGISTRY.get_sample_value("otterworks_document_operations_total", labels) or 0.0


@pytest.mark.asyncio
async def test_document_operations_are_counted(client: AsyncClient, owner_id: uuid.UUID):
    before = _ops("create", "ok")
    resp = await client.post(
        "/api/v1/documents/", json={"title": "T", "content": "", "owner_id": str(owner_id)}
    )
    assert resp.status_code == 201
    assert _ops("create", "ok") == before + 1

    metrics = (await client.get("/metrics")).text
    assert "otterworks_document_operation_duration_seconds_bucket" in metrics
