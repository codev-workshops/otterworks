"""Contract tests for the search-service against its OpenAPI spec.

Loads the OpenAPI spec from shared/openapi/search-service.yaml and validates
that a running search-service instance conforms to the documented contract.

Usage:
    SEARCH_SERVICE_URL=http://localhost:8087 pytest tests/contract/test_search_contract.py -v

Requirements:
    pip install pyyaml jsonschema requests pytest
"""

from __future__ import annotations

from typing import Any

import pytest
import requests

from tests.contract._openapi_utils import (
    load_spec,
    require_live_service,
    service_base_url,
    validate_response,
)

BASE_URL = service_base_url("SEARCH_SERVICE_URL", "http://localhost:8087")

require_live_service(BASE_URL, "SEARCH_SERVICE_URL")

_validate_response = validate_response


@pytest.fixture(scope="session")
def openapi_spec() -> dict[str, Any]:
    """Load and return the OpenAPI spec as a dict."""
    return load_spec("search-service")


class TestSearchEndpoint:
    """Tests for GET /api/v1/search/."""

    def test_search_requires_query(self, openapi_spec: dict[str, Any]) -> None:
        """Searching without q parameter returns 400."""
        resp = requests.get(
            f"{BASE_URL}/api/v1/search/",
            headers={"X-User-ID": "test-user-001"},
        )
        assert resp.status_code == 400
        body = resp.json()
        _validate_response(openapi_spec, body, "/api/v1/search/", "get", "400")
        assert "error" in body

    def test_search_invalid_page(self, openapi_spec: dict[str, Any]) -> None:
        """Invalid page parameter returns 400."""
        resp = requests.get(
            f"{BASE_URL}/api/v1/search/",
            params={"q": "test", "page": "abc"},
            headers={"X-User-ID": "test-user-001"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body

    def test_search_valid_query(self, openapi_spec: dict[str, Any]) -> None:
        """Valid search returns 200 with correct schema."""
        resp = requests.get(
            f"{BASE_URL}/api/v1/search/",
            params={"q": "test", "page": "1", "size": "10"},
            headers={"X-User-ID": "test-user-001"},
        )
        assert resp.status_code == 200
        body = resp.json()
        _validate_response(openapi_spec, body, "/api/v1/search/", "get", "200")
        assert "results" in body
        assert "total" in body
        assert "page" in body
        assert "page_size" in body
        assert "query" in body
        assert body["query"] == "test"


class TestSuggestEndpoint:
    """Tests for GET /api/v1/search/suggest."""

    def test_suggest_short_prefix(self, openapi_spec: dict[str, Any]) -> None:
        """Prefix shorter than 2 chars returns empty suggestions."""
        resp = requests.get(
            f"{BASE_URL}/api/v1/search/suggest",
            params={"q": "a"},
            headers={"X-User-ID": "test-user-001"},
        )
        assert resp.status_code == 200
        body = resp.json()
        _validate_response(openapi_spec, body, "/api/v1/search/suggest", "get", "200")
        assert body["suggestions"] == []
        assert body["query"] == "a"

    def test_suggest_valid_prefix(self, openapi_spec: dict[str, Any]) -> None:
        """Prefix of 2+ chars returns valid response schema."""
        resp = requests.get(
            f"{BASE_URL}/api/v1/search/suggest",
            params={"q": "tes"},
            headers={"X-User-ID": "test-user-001"},
        )
        assert resp.status_code == 200
        body = resp.json()
        _validate_response(openapi_spec, body, "/api/v1/search/suggest", "get", "200")
        assert isinstance(body["suggestions"], list)
        assert body["query"] == "tes"


class TestAdvancedSearchEndpoint:
    """Tests for POST /api/v1/search/advanced."""

    def test_advanced_search_empty_body(self, openapi_spec: dict[str, Any]) -> None:
        """Advanced search with empty body returns 200 (all fields optional)."""
        resp = requests.post(
            f"{BASE_URL}/api/v1/search/advanced",
            json={},
            headers={"X-User-ID": "test-user-001"},
        )
        assert resp.status_code == 200
        body = resp.json()
        _validate_response(openapi_spec, body, "/api/v1/search/advanced", "post", "200")

    def test_advanced_search_with_filters(self, openapi_spec: dict[str, Any]) -> None:
        """Advanced search with filters returns correct schema."""
        resp = requests.post(
            f"{BASE_URL}/api/v1/search/advanced",
            json={
                "q": "report",
                "type": "document",
                "tags": ["finance"],
                "date_from": "2024-01-01",
                "date_to": "2024-12-31",
                "page": 1,
                "size": 10,
            },
            headers={"X-User-ID": "test-user-001"},
        )
        assert resp.status_code == 200
        body = resp.json()
        _validate_response(openapi_spec, body, "/api/v1/search/advanced", "post", "200")
        assert "results" in body
        assert "total" in body

    def test_advanced_search_invalid_page(self, openapi_spec: dict[str, Any]) -> None:
        """Invalid page in advanced search body returns 400."""
        resp = requests.post(
            f"{BASE_URL}/api/v1/search/advanced",
            json={"page": "invalid"},
            headers={"X-User-ID": "test-user-001"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body


class TestHealthEndpoints:
    """Tests for /health and /health/ready."""

    def test_health_liveness(self, openapi_spec: dict[str, Any]) -> None:
        """Liveness endpoint returns alive status."""
        resp = requests.get(f"{BASE_URL}/health")
        assert resp.status_code == 200
        body = resp.json()
        _validate_response(openapi_spec, body, "/health", "get", "200")
        assert body["status"] == "alive"
        assert body["service"] == "search-service"

    def test_health_readiness(self, openapi_spec: dict[str, Any]) -> None:
        """Readiness endpoint returns valid schema (200 or 503)."""
        resp = requests.get(f"{BASE_URL}/health/ready")
        assert resp.status_code in (200, 503)
        body = resp.json()
        assert "ready" in body
        if resp.status_code == 200:
            _validate_response(openapi_spec, body, "/health/ready", "get", "200")
            assert body["ready"] is True
        else:
            _validate_response(openapi_spec, body, "/health/ready", "get", "503")
            assert body["ready"] is False
            assert body["reason"] == "meilisearch_unavailable"


class TestMetricsEndpoint:
    """Tests for /metrics."""

    def test_metrics_returns_prometheus_format(self, openapi_spec: dict[str, Any]) -> None:
        """Metrics endpoint returns text/plain Prometheus data."""
        resp = requests.get(f"{BASE_URL}/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("Content-Type", "")
        body = resp.text
        assert "search_service_requests_total" in body or "search_service" in body


class TestAnalyticsEndpoint:
    """Tests for GET /api/v1/search/analytics."""

    def test_analytics_response_schema(self, openapi_spec: dict[str, Any]) -> None:
        """Analytics endpoint returns valid schema."""
        resp = requests.get(
            f"{BASE_URL}/api/v1/search/analytics",
            headers={"X-User-ID": "test-user-001"},
        )
        assert resp.status_code == 200
        body = resp.json()
        _validate_response(openapi_spec, body, "/api/v1/search/analytics", "get", "200")
        assert "popular_queries" in body
        assert "zero_result_queries" in body
        assert "total_searches" in body
        assert "avg_results_per_query" in body
        assert isinstance(body["popular_queries"], list)
        assert isinstance(body["total_searches"], int)


class TestIndexEndpoints:
    """Tests for indexing endpoints."""

    def test_index_document_missing_body(self, openapi_spec: dict[str, Any]) -> None:
        """POST /api/v1/search/index/document without body returns 400."""
        resp = requests.post(
            f"{BASE_URL}/api/v1/search/index/document",
            headers={"Content-Type": "application/json", "X-User-ID": "test-user-001"},
        )
        # Flask returns 400 for missing JSON body
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body

    def test_index_document_missing_id(self, openapi_spec: dict[str, Any]) -> None:
        """POST /api/v1/search/index/document without id returns 400."""
        resp = requests.post(
            f"{BASE_URL}/api/v1/search/index/document",
            json={"title": "Test Doc"},
            headers={"X-User-ID": "test-user-001"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body

    def test_index_file_missing_body(self, openapi_spec: dict[str, Any]) -> None:
        """POST /api/v1/search/index/file without body returns 400."""
        resp = requests.post(
            f"{BASE_URL}/api/v1/search/index/file",
            headers={"Content-Type": "application/json", "X-User-ID": "test-user-001"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body

    def test_index_file_missing_name(self, openapi_spec: dict[str, Any]) -> None:
        """POST /api/v1/search/index/file without name returns 400."""
        resp = requests.post(
            f"{BASE_URL}/api/v1/search/index/file",
            json={"id": "file-001"},
            headers={"X-User-ID": "test-user-001"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body


class TestSpecCompleteness:
    """Meta-tests to verify the spec itself covers all implemented endpoints."""

    def test_spec_has_all_paths(self, openapi_spec: dict[str, Any]) -> None:
        """Verify the spec documents all expected endpoints."""
        paths = set(openapi_spec.get("paths", {}).keys())
        expected_paths = {
            "/api/v1/search/",
            "/api/v1/search/suggest",
            "/api/v1/search/advanced",
            "/api/v1/search/index/document",
            "/api/v1/search/index/file",
            "/api/v1/search/index/document/{id}",
            "/api/v1/search/index/file/{id}",
            "/api/v1/search/reindex",
            "/api/v1/search/analytics",
            "/health",
            "/health/ready",
            "/metrics",
        }
        missing = expected_paths - paths
        assert not missing, f"Missing paths in spec: {missing}"

    def test_spec_schemas_defined(self, openapi_spec: dict[str, Any]) -> None:
        """Verify key schemas are defined."""
        schemas = set(openapi_spec.get("components", {}).get("schemas", {}).keys())
        expected_schemas = {
            "SearchHit",
            "SearchResponse",
            "SuggestResponse",
            "AdvancedSearchRequest",
            "IndexDocumentRequest",
            "IndexFileRequest",
            "IndexResponse",
            "AnalyticsResponse",
            "HealthResponse",
            "ReadinessResponse",
            "ErrorResponse",
        }
        missing = expected_schemas - schemas
        assert not missing, f"Missing schemas in spec: {missing}"
