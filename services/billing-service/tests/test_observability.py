from __future__ import annotations

import io
import json
import re
from datetime import date
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import REGISTRY

import app.main as main
from app.domain import SubscriptionRow
from app.observability import REQUEST_ID_HEADER, configure_logging

TENANT = UUID("00000000-0000-0000-0000-000000000001")
PLAN = UUID("10000000-0000-0000-0000-000000000002")
CREATED = SubscriptionRow(
    UUID("20000000-0000-0000-0000-000000000003"),
    TENANT,
    PLAN,
    date(2026, 3, 1),
    None,
    "active",
    None,
)
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SPANS = InMemorySpanExporter()
trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(SPANS))


class FakeConnection:
    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, *_args: object) -> None:
        return None


class FakeRepository:
    def __init__(self, _connection: object) -> None:
        pass

    def find_entitlements(self, _tenant_id: UUID) -> list:
        return []


def sample(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "migrate", lambda: None)
    monkeypatch.setattr(main, "connect", FakeConnection)
    with TestClient(main.app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_metrics_serves_prometheus_text_with_http_metrics(client) -> None:
    client.get("/health")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE http_requests_total counter" in response.text
    assert 'handler="/health"' in response.text
    assert "http_request_duration_seconds_bucket" in response.text


def test_health_is_503_when_database_is_down(client, monkeypatch) -> None:
    assert client.get("/health").json() == {"status": "healthy", "service": "billing-service"}

    def unavailable() -> None:
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(main, "connect", unavailable)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}


def test_request_id_is_echoed_generated_and_replaced_when_invalid(client) -> None:
    assert (
        client.get("/health", headers={REQUEST_ID_HEADER: "abc-123.x"}).headers[REQUEST_ID_HEADER]
        == "abc-123.x"
    )
    assert UUID_RE.match(client.get("/health").headers[REQUEST_ID_HEADER])
    replaced = client.get("/health", headers={REQUEST_ID_HEADER: "bad id!"}).headers[
        REQUEST_ID_HEADER
    ]
    assert UUID_RE.match(replaced)


def test_logs_are_json_with_request_and_trace_ids_and_span_is_tagged(client, monkeypatch) -> None:
    monkeypatch.setattr(main, "entitlement", lambda *_args: None)
    monkeypatch.setattr(main, "PostgresPlansRepository", FakeRepository)
    stream = io.StringIO()
    configure_logging("json", stream=stream)
    SPANS.clear()
    try:
        response = client.get(
            f"/api/tenants/{TENANT}/entitlement",
            params={"on": "2026-03-01"},
            headers={REQUEST_ID_HEADER: "req-42"},
        )
    finally:
        configure_logging("json")
    assert response.status_code == 404
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    access = next(r for r in records if r["event"] == "http_request")
    domain = next(r for r in records if r["event"] == "entitlement_lookup")
    assert access["service"] == "billing-service"
    assert access["request_id"] == domain["request_id"] == "req-42"
    assert access["handler"] == "/api/tenants/{tenant_id}/entitlement"
    assert access["status"] == 404
    server_span = next(
        s for s in SPANS.get_finished_spans() if s.attributes.get("request.id") == "req-42"
    )
    assert access["trace_id"] == format(server_span.context.trace_id, "032x")
    assert domain["trace_id"] == access["trace_id"]


def test_unhandled_error_returns_json_500_without_detail(client, monkeypatch) -> None:
    def boom() -> None:
        raise RuntimeError("secret connection string")

    monkeypatch.setattr(main, "connect", boom)
    response = client.get("/api/plans", headers={REQUEST_ID_HEADER: "req-500"})
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error", "request_id": "req-500"}
    assert response.headers[REQUEST_ID_HEADER] == "req-500"


def test_plan_change_and_entitlement_domain_metrics(client, monkeypatch) -> None:
    calls = 0

    def fake_change_plan(*_args: object) -> tuple[list[SubscriptionRow], SubscriptionRow]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise psycopg.errors.UniqueViolation("duplicate subscription")
        return [CREATED], CREATED

    monkeypatch.setattr(main, "change_plan", fake_change_plan)
    monkeypatch.setattr(main, "PostgresPlansRepository", FakeRepository)
    monkeypatch.setattr(main, "entitlement", lambda *_args: None)
    created = sample("billing_plan_changes_total", {"outcome": "created"})
    conflict = sample("billing_plan_changes_total", {"outcome": "conflict"})
    not_found = sample("billing_entitlement_lookups_total", {"outcome": "not_found"})

    payload = {"plan_id": str(PLAN), "effective_on": "2026-03-01"}
    client.post(f"/api/tenants/{TENANT}/plan-change", json=payload)
    client.post(f"/api/tenants/{TENANT}/plan-change", json=payload)
    client.get(f"/api/tenants/{TENANT}/entitlement", params={"on": "2026-03-01"})

    assert sample("billing_plan_changes_total", {"outcome": "created"}) == created + 1
    assert sample("billing_plan_changes_total", {"outcome": "conflict"}) == conflict + 1
    assert sample("billing_entitlement_lookups_total", {"outcome": "not_found"}) == not_found + 1
    assert sample("billing_plan_change_duration_seconds_count", {"outcome": "created"}) >= 1
