"""Contract tests for the notification-service against its OpenAPI spec.

Loads the OpenAPI spec from shared/openapi/notification-service.yaml and
validates that a running notification-service instance conforms to the
documented contract.

Usage:
    NOTIFICATION_SERVICE_URL=http://localhost:8086 pytest tests/contract/test_notification_contract.py -v

The REST API has no create endpoint (notifications are produced from SQS
events), so tests that need an existing notification use the first one
returned for NOTIFICATION_TEST_USER_ID (default test-user-001) and skip when
that user has none.

Requirements:
    pip install pyyaml jsonschema requests pytest
"""

from __future__ import annotations

import os
from typing import Any

import pytest
import requests

from tests.contract._openapi_utils import (
    assert_documented_status,
    load_spec,
    require_live_service,
    service_base_url,
    validate_response,
)

BASE_URL = service_base_url("NOTIFICATION_SERVICE_URL", "http://localhost:8086")
USER_ID = os.environ.get("NOTIFICATION_TEST_USER_ID", "test-user-001")
MISSING_ID = "contract-test-missing-notification"

NOTIFS = "/api/v1/notifications"
USER_HEADERS = {"X-User-ID": USER_ID}

require_live_service(BASE_URL, "NOTIFICATION_SERVICE_URL")


@pytest.fixture(scope="session")
def openapi_spec() -> dict[str, Any]:
    """Load and return the OpenAPI spec as a dict."""
    return load_spec("notification-service")


@pytest.fixture(scope="module")
def existing_notification(openapi_spec: dict[str, Any]) -> dict[str, Any]:
    resp = requests.get(f"{BASE_URL}{NOTIFS}", headers=USER_HEADERS)
    assert resp.status_code == 200
    data = resp.json()["data"]
    if not data:
        pytest.skip(f"user {USER_ID} has no notifications; seed one to run this test")
    return data[0]


class TestListNotifications:
    """GET /api/v1/notifications."""

    def test_list_with_header(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{NOTIFS}", headers=USER_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        validate_response(openapi_spec, body, NOTIFS, "get", "200")
        assert body["page"] == 1
        assert body["pageSize"] == 20

    def test_list_with_query_param_and_paging(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(
            f"{BASE_URL}{NOTIFS}", params={"user_id": USER_ID, "page": 2, "page_size": 5}
        )
        assert resp.status_code == 200
        body = resp.json()
        validate_response(openapi_spec, body, NOTIFS, "get", "200")
        assert body["page"] == 2
        assert body["pageSize"] == 5
        assert body["hasMore"] == (body["page"] * body["pageSize"] < body["total"])

    def test_list_requires_user(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{NOTIFS}")
        assert resp.status_code == 400
        body = resp.json()
        validate_response(openapi_spec, body, NOTIFS, "get", "400")
        assert "user_id is required" in body["error"]


class TestUnreadCount:
    """GET /api/v1/notifications/unread-count."""

    PATH = f"{NOTIFS}/unread-count"

    def test_unread_count(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{self.PATH}", headers=USER_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        validate_response(openapi_spec, body, self.PATH, "get", "200")
        assert body["userId"] == USER_ID
        assert body["unreadCount"] >= 0

    def test_unread_count_requires_user(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{self.PATH}")
        assert resp.status_code == 400
        validate_response(openapi_spec, resp.json(), self.PATH, "get", "400")


class TestGetNotification:
    """GET /api/v1/notifications/{id}."""

    PATH = f"{NOTIFS}/{{id}}"

    def test_get_existing(
        self, openapi_spec: dict[str, Any], existing_notification: dict[str, Any]
    ) -> None:
        resp = requests.get(f"{BASE_URL}{NOTIFS}/{existing_notification['id']}")
        assert resp.status_code == 200
        body = resp.json()
        validate_response(openapi_spec, body, self.PATH, "get", "200")
        assert body["id"] == existing_notification["id"]

    def test_get_not_found(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{NOTIFS}/{MISSING_ID}")
        assert resp.status_code == 404
        body = resp.json()
        validate_response(openapi_spec, body, self.PATH, "get", "404")
        assert body["error"] == "Notification not found"


class TestMarkRead:
    """PUT /api/v1/notifications/{id}/read and PUT /api/v1/notifications/read-all."""

    READ = f"{NOTIFS}/{{id}}/read"
    READ_ALL = f"{NOTIFS}/read-all"

    def test_mark_read_existing(
        self, openapi_spec: dict[str, Any], existing_notification: dict[str, Any]
    ) -> None:
        resp = requests.put(f"{BASE_URL}{NOTIFS}/{existing_notification['id']}/read")
        assert resp.status_code == 204
        assert resp.content == b""
        assert_documented_status(openapi_spec, self.READ, "put", 204)

        after = requests.get(f"{BASE_URL}{NOTIFS}/{existing_notification['id']}")
        assert after.status_code == 200
        assert after.json()["read"] is True

    def test_mark_read_not_found(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.put(f"{BASE_URL}{NOTIFS}/{MISSING_ID}/read")
        assert resp.status_code == 404
        validate_response(openapi_spec, resp.json(), self.READ, "put", "404")

    def test_mark_all_read(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.put(f"{BASE_URL}{self.READ_ALL}", headers=USER_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        validate_response(openapi_spec, body, self.READ_ALL, "put", "200")
        assert body["markedCount"] >= 0

        unread = requests.get(f"{BASE_URL}{NOTIFS}/unread-count", headers=USER_HEADERS)
        assert unread.json()["unreadCount"] == 0

    def test_mark_all_read_requires_user(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.put(f"{BASE_URL}{self.READ_ALL}")
        assert resp.status_code == 400
        validate_response(openapi_spec, resp.json(), self.READ_ALL, "put", "400")


class TestDeleteNotification:
    """DELETE /api/v1/notifications/{id}."""

    PATH = f"{NOTIFS}/{{id}}"

    def test_delete_not_found(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.delete(f"{BASE_URL}{NOTIFS}/{MISSING_ID}")
        assert resp.status_code == 404
        validate_response(openapi_spec, resp.json(), self.PATH, "delete", "404")


class TestPreferences:
    """/api/v1/preferences."""

    PATH = "/api/v1/preferences"

    def test_get_preferences(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{self.PATH}", headers=USER_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        validate_response(openapi_spec, body, self.PATH, "get", "200")
        assert body["userId"] == USER_ID

    def test_get_preferences_requires_user(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{self.PATH}")
        assert resp.status_code == 400
        validate_response(openapi_spec, resp.json(), self.PATH, "get", "400")

    def test_update_preferences_roundtrip(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.put(
            f"{BASE_URL}{self.PATH}",
            json={"userId": USER_ID, "eventType": "comment_added", "channels": ["IN_APP"]},
        )
        assert resp.status_code == 204
        assert_documented_status(openapi_spec, self.PATH, "put", 204)

        prefs = requests.get(f"{BASE_URL}{self.PATH}", headers=USER_HEADERS).json()
        assert prefs["channels"]["comment_added"] == ["IN_APP"]

    def test_update_preferences_malformed_body(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.put(
            f"{BASE_URL}{self.PATH}", data="not json", headers={"Content-Type": "application/json"}
        )
        assert resp.status_code == 500
        validate_response(openapi_spec, resp.json(), self.PATH, "put", "500")


class TestHealthEndpoints:
    """/health and /metrics."""

    def test_health(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}/health")
        assert resp.status_code == 200
        body = resp.json()
        validate_response(openapi_spec, body, "/health", "get", "200")
        assert body["status"] == "healthy"
        assert body["service"] == "notification-service"

    def test_metrics(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("Content-Type", "")


class TestSpecCompleteness:
    """Meta-tests mirroring Routes.kt (Ktor has no auto-generated spec to diff against)."""

    def test_spec_has_all_paths(self, openapi_spec: dict[str, Any]) -> None:
        expected = {
            "/api/v1/notifications": {"get"},
            "/api/v1/notifications/unread-count": {"get"},
            "/api/v1/notifications/{id}": {"get", "delete"},
            "/api/v1/notifications/{id}/read": {"put"},
            "/api/v1/notifications/read-all": {"put"},
            "/api/v1/preferences": {"get", "put"},
            "/health": {"get"},
            "/metrics": {"get"},
            "/ws/notifications/{userId}": {"get"},
        }
        paths = openapi_spec.get("paths", {})
        missing = sorted(
            f"{m.upper()} {p}" for p, methods in expected.items() for m in methods if m not in paths.get(p, {})
        )
        assert not missing, f"Missing operations in spec: {missing}"
        assert set(paths) == set(expected), f"Unexpected paths: {set(paths) ^ set(expected)}"

    def test_spec_schemas_defined(self, openapi_spec: dict[str, Any]) -> None:
        schemas = set(openapi_spec.get("components", {}).get("schemas", {}).keys())
        expected = {
            "ErrorResponse",
            "Notification",
            "PaginatedNotificationResponse",
            "UnreadCountResponse",
            "MarkAllReadResponse",
            "NotificationPreference",
            "NotificationPreferenceRequest",
            "HealthResponse",
        }
        missing = expected - schemas
        assert not missing, f"Missing schemas in spec: {missing}"
