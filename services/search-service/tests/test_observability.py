"""Tests for request IDs, trace-correlated JSON logs, error handling and query metrics."""

from __future__ import annotations

import json
import logging
import re

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import REGISTRY

from app.observability import REQUEST_ID_HEADER

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@pytest.fixture()
def spans(app):
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    exporter.shutdown()


def _json_logs(caplog) -> list[dict]:
    entries = []
    for record in caplog.records:
        try:
            entries.append(json.loads(record.getMessage()))
        except (TypeError, ValueError):
            continue
    return entries


class TestRequestId:
    def test_valid_request_id_is_echoed(self, client):
        response = client.get("/health", headers={REQUEST_ID_HEADER: "abc-123.x:1"})
        assert response.headers[REQUEST_ID_HEADER] == "abc-123.x:1"

    def test_missing_request_id_is_generated(self, client):
        assert UUID_RE.match(client.get("/health").headers[REQUEST_ID_HEADER])

    def test_invalid_request_id_is_replaced(self, client):
        response = client.get("/health", headers={REQUEST_ID_HEADER: "bad id <script>"})
        assert UUID_RE.match(response.headers[REQUEST_ID_HEADER])


class TestStructuredLogs:
    def test_logs_are_json_with_request_and_trace_ids(self, client, spans, caplog):
        caplog.set_level(logging.DEBUG)
        response = client.get("/api/v1/search/?q=otter", headers={REQUEST_ID_HEADER: "req-42"})
        assert response.status_code == 200

        [server_span] = [s for s in spans.get_finished_spans() if s.kind.name == "SERVER"]
        assert server_span.attributes["request.id"] == "req-42"
        trace_id = format(server_span.context.trace_id, "032x")

        logs = _json_logs(caplog)
        executed = next(e for e in logs if e["event"] == "search_executed")
        assert executed["request_id"] == "req-42"
        assert executed["trace_id"] == trace_id
        assert executed["service"] == "search-service"
        assert executed["outcome"] == "empty"

        access = next(e for e in logs if e["event"] == "http_request")
        assert access["request_id"] == "req-42"
        assert access["trace_id"] == trace_id
        assert access["route"] == "/api/v1/search/"
        assert access["status"] == 200
        assert isinstance(access["duration_ms"], float)

    def test_probe_requests_log_at_debug(self, client, caplog):
        caplog.set_level(logging.DEBUG)
        client.get("/health")
        access = next(e for e in _json_logs(caplog) if e["event"] == "http_request")
        assert access["level"] == "debug"


class TestErrorHandling:
    def test_unhandled_error_returns_json_500_without_details(self, app, caplog):
        @app.route("/boom")
        def boom():
            raise RuntimeError("meili key=hunter2 leaked")

        response = app.test_client().get("/boom", headers={REQUEST_ID_HEADER: "req-500"})
        assert response.status_code == 500
        assert response.get_json() == {"error": "Internal server error", "request_id": "req-500"}
        assert b"hunter2" not in response.data
        assert response.headers[REQUEST_ID_HEADER] == "req-500"

    def test_http_errors_keep_default_handling(self, client):
        response = client.get("/does-not-exist")
        assert response.status_code == 404


class TestQueryMetrics:
    @staticmethod
    def _count(kind: str, outcome: str) -> float:
        value = REGISTRY.get_sample_value(
            "search_service_query_duration_seconds_count", {"kind": kind, "outcome": outcome}
        )
        return value or 0.0

    def test_search_records_duration_and_outcome(self, client):
        before = self._count("search", "empty")
        assert client.get("/api/v1/search/?q=otter").status_code == 200
        assert self._count("search", "empty") == before + 1

    def test_search_failure_is_counted(self, client, mock_meilisearch_client):
        mock_meilisearch_client.index.return_value.search.side_effect = RuntimeError("down")
        before = self._count("search", "error")
        assert client.get("/api/v1/search/?q=otter").status_code == 500
        assert self._count("search", "error") == before + 1

    def test_metrics_endpoint_exposes_query_metrics(self, client):
        client.get("/api/v1/search/?q=otter")
        body = client.get("/metrics").data.decode()
        assert "search_service_query_duration_seconds_bucket" in body
        assert "search_service_query_results_bucket" in body
