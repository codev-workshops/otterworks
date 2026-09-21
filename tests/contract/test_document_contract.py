"""Contract tests for the document-service against its OpenAPI spec.

Loads the OpenAPI spec from shared/openapi/document-service.yaml and validates
that a running document-service instance conforms to the documented contract.

Usage:
    DOCUMENT_SERVICE_URL=http://localhost:8083 pytest tests/contract/test_document_contract.py -v

Authentication:
    The service accepts ``Authorization: Bearer <jwt>``. When it runs without a
    JWT_SECRET it falls back to the ``X-User-ID`` header (still requiring the
    Bearer prefix). Set DOCUMENT_SERVICE_JWT_SECRET to the service's secret to
    exercise the real JWT path (requires PyJWT); otherwise the fallback is used.

Requirements:
    pip install pyyaml jsonschema requests pytest
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
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

BASE_URL = service_base_url("DOCUMENT_SERVICE_URL", "http://localhost:8083")
JWT_SECRET = os.environ.get("DOCUMENT_SERVICE_JWT_SECRET", "")

OWNER_ID = str(uuid.uuid4())
OTHER_USER_ID = str(uuid.uuid4())
MISSING_ID = "00000000-0000-4000-8000-000000000000"

DOCS = "/api/v1/documents"

require_live_service(BASE_URL, "DOCUMENT_SERVICE_URL")


def _auth(user_id: str) -> dict[str, str]:
    if JWT_SECRET:
        import jwt

        token = jwt.encode({"user_id": user_id}, JWT_SECRET, algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}
    return {"Authorization": "Bearer contract-test", "X-User-ID": user_id}


OWNER = _auth(OWNER_ID)
OTHER = _auth(OTHER_USER_ID)


@pytest.fixture(scope="session")
def openapi_spec() -> dict[str, Any]:
    """Load and return the OpenAPI spec as a dict."""
    return load_spec("document-service")


@pytest.fixture(scope="module")
def document(openapi_spec: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """A document owned by OWNER_ID, deleted after the module finishes."""
    resp = requests.post(
        f"{BASE_URL}{DOCS}/",
        json={"title": "Contract test doc", "content": "hello world", "owner_id": OWNER_ID},
        headers=OWNER,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    validate_response(openapi_spec, body, f"{DOCS}/", "post", "201")
    yield body
    requests.delete(f"{BASE_URL}{DOCS}/{body['id']}", headers=OWNER)


@pytest.fixture(scope="module")
def template(openapi_spec: dict[str, Any]) -> dict[str, Any]:
    resp = requests.post(
        f"{BASE_URL}/api/v1/templates/",
        json={
            "name": "Contract template",
            "description": "used by contract tests",
            "content": "# {{title}}",
            "created_by": OWNER_ID,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    validate_response(openapi_spec, body, "/api/v1/templates/", "post", "201")
    return body


class TestDocumentCollection:
    """GET/POST /api/v1/documents/."""

    def test_list_documents(self, openapi_spec: dict[str, Any], document: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{DOCS}/", params={"owner_id": OWNER_ID}, headers=OWNER)
        assert resp.status_code == 200
        body = resp.json()
        validate_response(openapi_spec, body, f"{DOCS}/", "get", "200")
        assert body["total"] >= 1
        assert any(item["id"] == document["id"] for item in body["items"])

    def test_list_documents_with_filters(
        self, openapi_spec: dict[str, Any], document: dict[str, Any]
    ) -> None:
        resp = requests.get(
            f"{BASE_URL}{DOCS}/",
            params={"owner_id": OWNER_ID, "title": "Contract", "sort": "title", "direction": "asc"},
            headers=OWNER,
        )
        assert resp.status_code == 200
        body = resp.json()
        validate_response(openapi_spec, body, f"{DOCS}/", "get", "200")
        assert any(item["id"] == document["id"] for item in body["items"])

    def test_list_documents_invalid_page(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{DOCS}/", params={"page": 0}, headers=OWNER)
        assert resp.status_code == 422
        validate_response(openapi_spec, resp.json(), f"{DOCS}/", "get", "422")

    def test_create_document_requires_owner(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.post(f"{BASE_URL}{DOCS}/", json={"title": "No owner"})
        assert resp.status_code == 401
        validate_response(openapi_spec, resp.json(), f"{DOCS}/", "post", "401")

    def test_create_document_validation(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.post(f"{BASE_URL}{DOCS}/", json={"title": ""}, headers=OWNER)
        assert resp.status_code == 422
        validate_response(openapi_spec, resp.json(), f"{DOCS}/", "post", "422")

    def test_search_documents(self, openapi_spec: dict[str, Any], document: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{DOCS}/search", params={"q": "Contract"})
        assert resp.status_code == 200
        validate_response(openapi_spec, resp.json(), f"{DOCS}/search", "get", "200")

    def test_search_requires_query(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{DOCS}/search")
        assert resp.status_code == 422
        validate_response(openapi_spec, resp.json(), f"{DOCS}/search", "get", "422")


class TestDocumentItem:
    """GET/PUT/PATCH/DELETE /api/v1/documents/{document_id}."""

    PATH = f"{DOCS}/{{document_id}}"

    def test_get_document(self, openapi_spec: dict[str, Any], document: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{DOCS}/{document['id']}", headers=OWNER)
        assert resp.status_code == 200
        body = resp.json()
        validate_response(openapi_spec, body, self.PATH, "get", "200")
        assert body["id"] == document["id"]

    def test_get_document_unauthenticated(
        self, openapi_spec: dict[str, Any], document: dict[str, Any]
    ) -> None:
        resp = requests.get(f"{BASE_URL}{DOCS}/{document['id']}")
        assert resp.status_code == 401
        validate_response(openapi_spec, resp.json(), self.PATH, "get", "401")

    def test_get_document_forbidden(
        self, openapi_spec: dict[str, Any], document: dict[str, Any]
    ) -> None:
        resp = requests.get(f"{BASE_URL}{DOCS}/{document['id']}", headers=OTHER)
        assert resp.status_code == 403
        validate_response(openapi_spec, resp.json(), self.PATH, "get", "403")

    def test_get_document_not_found(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{DOCS}/{MISSING_ID}", headers=OWNER)
        assert resp.status_code == 404
        validate_response(openapi_spec, resp.json(), self.PATH, "get", "404")

    def test_update_document(self, openapi_spec: dict[str, Any], document: dict[str, Any]) -> None:
        resp = requests.put(
            f"{BASE_URL}{DOCS}/{document['id']}",
            json={"title": "Replaced title", "content": "replaced body"},
            headers=OWNER,
        )
        assert resp.status_code == 200
        body = resp.json()
        validate_response(openapi_spec, body, self.PATH, "put", "200")
        assert body["title"] == "Replaced title"

    def test_update_document_validation(
        self, openapi_spec: dict[str, Any], document: dict[str, Any]
    ) -> None:
        resp = requests.put(f"{BASE_URL}{DOCS}/{document['id']}", json={}, headers=OWNER)
        assert resp.status_code == 422
        validate_response(openapi_spec, resp.json(), self.PATH, "put", "422")

    def test_update_document_not_found(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.put(f"{BASE_URL}{DOCS}/{MISSING_ID}", json={"title": "x"}, headers=OWNER)
        assert resp.status_code == 404
        validate_response(openapi_spec, resp.json(), self.PATH, "put", "404")

    def test_patch_document(self, openapi_spec: dict[str, Any], document: dict[str, Any]) -> None:
        resp = requests.patch(
            f"{BASE_URL}{DOCS}/{document['id']}", json={"content": "patched body"}, headers=OWNER
        )
        assert resp.status_code == 200
        body = resp.json()
        validate_response(openapi_spec, body, self.PATH, "patch", "200")
        assert body["content"] == "patched body"

    def test_patch_document_rejects_null_title(
        self, openapi_spec: dict[str, Any], document: dict[str, Any]
    ) -> None:
        resp = requests.patch(
            f"{BASE_URL}{DOCS}/{document['id']}", json={"title": None}, headers=OWNER
        )
        assert resp.status_code == 422
        validate_response(openapi_spec, resp.json(), self.PATH, "patch", "422")

    def test_patch_document_forbidden(
        self, openapi_spec: dict[str, Any], document: dict[str, Any]
    ) -> None:
        resp = requests.patch(
            f"{BASE_URL}{DOCS}/{document['id']}", json={"content": "x"}, headers=OTHER
        )
        assert resp.status_code == 403
        validate_response(openapi_spec, resp.json(), self.PATH, "patch", "403")

    def test_delete_document_unauthenticated(
        self, openapi_spec: dict[str, Any], document: dict[str, Any]
    ) -> None:
        resp = requests.delete(f"{BASE_URL}{DOCS}/{document['id']}")
        assert resp.status_code == 401
        validate_response(openapi_spec, resp.json(), self.PATH, "delete", "401")

    def test_delete_document_not_found(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.delete(f"{BASE_URL}{DOCS}/{MISSING_ID}", headers=OWNER)
        assert resp.status_code == 404
        validate_response(openapi_spec, resp.json(), self.PATH, "delete", "404")

    def test_delete_document(self, openapi_spec: dict[str, Any]) -> None:
        created = requests.post(
            f"{BASE_URL}{DOCS}/", json={"title": "To delete", "owner_id": OWNER_ID}, headers=OWNER
        )
        assert created.status_code == 201
        doc_id = created.json()["id"]
        resp = requests.delete(f"{BASE_URL}{DOCS}/{doc_id}", headers=OWNER)
        assert resp.status_code == 204
        assert resp.content == b""
        assert_documented_status(openapi_spec, self.PATH, "delete", 204)
        assert requests.get(f"{BASE_URL}{DOCS}/{doc_id}", headers=OWNER).status_code == 404


class TestVersions:
    """/api/v1/documents/{document_id}/versions[...]."""

    PATH = f"{DOCS}/{{document_id}}/versions"
    RESTORE = f"{DOCS}/{{document_id}}/versions/{{version_id}}/restore"

    def test_list_versions(self, openapi_spec: dict[str, Any], document: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{DOCS}/{document['id']}/versions", headers=OWNER)
        assert resp.status_code == 200
        body = resp.json()
        validate_response(openapi_spec, body, self.PATH, "get", "200")
        assert isinstance(body, list)

    def test_list_versions_forbidden(
        self, openapi_spec: dict[str, Any], document: dict[str, Any]
    ) -> None:
        resp = requests.get(f"{BASE_URL}{DOCS}/{document['id']}/versions", headers=OTHER)
        assert resp.status_code == 403
        validate_response(openapi_spec, resp.json(), self.PATH, "get", "403")

    def test_list_versions_not_found(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{DOCS}/{MISSING_ID}/versions", headers=OWNER)
        assert resp.status_code == 404
        validate_response(openapi_spec, resp.json(), self.PATH, "get", "404")

    def test_restore_version(self, openapi_spec: dict[str, Any], document: dict[str, Any]) -> None:
        requests.put(
            f"{BASE_URL}{DOCS}/{document['id']}",
            json={"title": "Versioned", "content": "v-next"},
            headers=OWNER,
        )
        versions = requests.get(f"{BASE_URL}{DOCS}/{document['id']}/versions", headers=OWNER)
        assert versions.status_code == 200
        if not versions.json():
            pytest.skip("service produced no versions to restore")
        version_id = versions.json()[0]["id"]
        resp = requests.post(
            f"{BASE_URL}{DOCS}/{document['id']}/versions/{version_id}/restore", headers=OWNER
        )
        assert resp.status_code == 200
        validate_response(openapi_spec, resp.json(), self.RESTORE, "post", "200")

    def test_restore_missing_version(
        self, openapi_spec: dict[str, Any], document: dict[str, Any]
    ) -> None:
        resp = requests.post(
            f"{BASE_URL}{DOCS}/{document['id']}/versions/{MISSING_ID}/restore", headers=OWNER
        )
        assert resp.status_code == 404
        validate_response(openapi_spec, resp.json(), self.RESTORE, "post", "404")


class TestExport:
    """GET /api/v1/documents/{document_id}/export and GET /api/v1/documents/exports."""

    PATH = f"{DOCS}/{{document_id}}/export"

    @pytest.mark.parametrize(
        ("fmt", "media"), [("markdown", "text/"), ("html", "text/html"), ("pdf", "application/pdf")]
    )
    def test_export_formats(
        self, openapi_spec: dict[str, Any], document: dict[str, Any], fmt: str, media: str
    ) -> None:
        resp = requests.get(
            f"{BASE_URL}{DOCS}/{document['id']}/export", params={"format": fmt}, headers=OWNER
        )
        assert resp.status_code == 200
        assert_documented_status(openapi_spec, self.PATH, "get", 200)
        assert media in resp.headers.get("Content-Type", "")

    def test_export_invalid_format(
        self, openapi_spec: dict[str, Any], document: dict[str, Any]
    ) -> None:
        resp = requests.get(
            f"{BASE_URL}{DOCS}/{document['id']}/export", params={"format": "docx"}, headers=OWNER
        )
        assert resp.status_code == 422
        validate_response(openapi_spec, resp.json(), self.PATH, "get", "422")

    def test_export_unauthenticated(
        self, openapi_spec: dict[str, Any], document: dict[str, Any]
    ) -> None:
        resp = requests.get(f"{BASE_URL}{DOCS}/{document['id']}/export")
        assert resp.status_code == 401
        validate_response(openapi_spec, resp.json(), self.PATH, "get", "401")

    def test_export_not_found(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{DOCS}/{MISSING_ID}/export", headers=OWNER)
        assert resp.status_code == 404
        validate_response(openapi_spec, resp.json(), self.PATH, "get", "404")

    def test_read_export_not_found(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{DOCS}/exports", params={"name": f"missing-{uuid.uuid4()}"})
        assert resp.status_code == 404
        validate_response(openapi_spec, resp.json(), f"{DOCS}/exports", "get", "404")

    def test_read_export_requires_name(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{DOCS}/exports")
        assert resp.status_code == 422
        validate_response(openapi_spec, resp.json(), f"{DOCS}/exports", "get", "422")


class TestShareLinks:
    """POST /api/v1/documents/{document_id}/share and GET /api/v1/documents/shared."""

    SHARE = f"{DOCS}/{{document_id}}/share"
    SHARED = f"{DOCS}/shared"

    def test_share_and_read_shared(
        self, openapi_spec: dict[str, Any], document: dict[str, Any]
    ) -> None:
        resp = requests.post(f"{BASE_URL}{DOCS}/{document['id']}/share", headers=OWNER)
        assert resp.status_code == 200
        link = resp.json()
        validate_response(openapi_spec, link, self.SHARE, "post", "200")
        assert link["document_id"] == document["id"]

        shared = requests.get(
            f"{BASE_URL}{self.SHARED}",
            params={"document_id": document["id"], "token": link["token"]},
        )
        assert shared.status_code == 200
        body = shared.json()
        validate_response(openapi_spec, body, self.SHARED, "get", "200")
        assert body["id"] == document["id"]

    def test_share_unauthenticated(
        self, openapi_spec: dict[str, Any], document: dict[str, Any]
    ) -> None:
        resp = requests.post(f"{BASE_URL}{DOCS}/{document['id']}/share")
        assert resp.status_code == 401
        validate_response(openapi_spec, resp.json(), self.SHARE, "post", "401")

    def test_share_forbidden(self, openapi_spec: dict[str, Any], document: dict[str, Any]) -> None:
        resp = requests.post(f"{BASE_URL}{DOCS}/{document['id']}/share", headers=OTHER)
        assert resp.status_code == 403
        validate_response(openapi_spec, resp.json(), self.SHARE, "post", "403")

    def test_share_not_found(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.post(f"{BASE_URL}{DOCS}/{MISSING_ID}/share", headers=OWNER)
        assert resp.status_code == 404
        validate_response(openapi_spec, resp.json(), self.SHARE, "post", "404")

    def test_shared_invalid_token(
        self, openapi_spec: dict[str, Any], document: dict[str, Any]
    ) -> None:
        resp = requests.get(
            f"{BASE_URL}{self.SHARED}", params={"document_id": document["id"], "token": "bogus"}
        )
        assert resp.status_code == 403
        validate_response(openapi_spec, resp.json(), self.SHARED, "get", "403")

    def test_shared_requires_params(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{self.SHARED}")
        assert resp.status_code == 422
        validate_response(openapi_spec, resp.json(), self.SHARED, "get", "422")


class TestComments:
    """/api/v1/documents/{document_id}/comments[/{comment_id}]."""

    PATH = f"{DOCS}/{{document_id}}/comments"
    ITEM = f"{DOCS}/{{document_id}}/comments/{{comment_id}}"

    def test_comment_lifecycle(self, openapi_spec: dict[str, Any], document: dict[str, Any]) -> None:
        created = requests.post(
            f"{BASE_URL}{DOCS}/{document['id']}/comments",
            json={"author_id": OWNER_ID, "content": "Looks good"},
        )
        assert created.status_code == 201, created.text
        comment = created.json()
        validate_response(openapi_spec, comment, self.PATH, "post", "201")

        listed = requests.get(f"{BASE_URL}{DOCS}/{document['id']}/comments")
        assert listed.status_code == 200
        validate_response(openapi_spec, listed.json(), self.PATH, "get", "200")
        assert any(c["id"] == comment["id"] for c in listed.json())

        deleted = requests.delete(f"{BASE_URL}{DOCS}/{document['id']}/comments/{comment['id']}")
        assert deleted.status_code == 204
        assert_documented_status(openapi_spec, self.ITEM, "delete", 204)

    def test_add_comment_document_not_found(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.post(
            f"{BASE_URL}{DOCS}/{MISSING_ID}/comments",
            json={"author_id": OWNER_ID, "content": "orphan"},
        )
        assert resp.status_code == 404
        validate_response(openapi_spec, resp.json(), self.PATH, "post", "404")

    def test_add_comment_validation(
        self, openapi_spec: dict[str, Any], document: dict[str, Any]
    ) -> None:
        resp = requests.post(f"{BASE_URL}{DOCS}/{document['id']}/comments", json={"content": ""})
        assert resp.status_code == 422
        validate_response(openapi_spec, resp.json(), self.PATH, "post", "422")

    def test_delete_comment_not_found(
        self, openapi_spec: dict[str, Any], document: dict[str, Any]
    ) -> None:
        resp = requests.delete(f"{BASE_URL}{DOCS}/{document['id']}/comments/{MISSING_ID}")
        assert resp.status_code == 404
        validate_response(openapi_spec, resp.json(), self.ITEM, "delete", "404")


class TestTemplates:
    """/api/v1/templates/ and /api/v1/documents/from-template/{template_id}."""

    PATH = "/api/v1/templates/"
    FROM_TEMPLATE = f"{DOCS}/from-template/{{template_id}}"

    def test_list_templates(self, openapi_spec: dict[str, Any], template: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}{self.PATH}")
        assert resp.status_code == 200
        body = resp.json()
        validate_response(openapi_spec, body, self.PATH, "get", "200")
        assert any(t["id"] == template["id"] for t in body)

    def test_create_template_validation(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.post(f"{BASE_URL}{self.PATH}", json={"name": ""})
        assert resp.status_code == 422
        validate_response(openapi_spec, resp.json(), self.PATH, "post", "422")

    def test_create_from_template(
        self, openapi_spec: dict[str, Any], template: dict[str, Any]
    ) -> None:
        resp = requests.post(
            f"{BASE_URL}{DOCS}/from-template/{template['id']}",
            json={"title": "From template", "owner_id": OWNER_ID},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        validate_response(openapi_spec, body, self.FROM_TEMPLATE, "post", "201")
        requests.delete(f"{BASE_URL}{DOCS}/{body['id']}", headers=OWNER)

    def test_create_from_missing_template(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.post(
            f"{BASE_URL}{DOCS}/from-template/{MISSING_ID}",
            json={"title": "Orphan", "owner_id": OWNER_ID},
        )
        assert resp.status_code == 404
        validate_response(openapi_spec, resp.json(), self.FROM_TEMPLATE, "post", "404")

    def test_create_from_template_validation(
        self, openapi_spec: dict[str, Any], template: dict[str, Any]
    ) -> None:
        resp = requests.post(f"{BASE_URL}{DOCS}/from-template/{template['id']}", json={})
        assert resp.status_code == 422
        validate_response(openapi_spec, resp.json(), self.FROM_TEMPLATE, "post", "422")


class TestHealthEndpoints:
    """/health and /metrics."""

    def test_health(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}/health")
        assert resp.status_code == 200
        body = resp.json()
        validate_response(openapi_spec, body, "/health", "get", "200")
        assert body["service"] == "document-service"

    def test_metrics(self, openapi_spec: dict[str, Any]) -> None:
        resp = requests.get(f"{BASE_URL}/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("Content-Type", "")
        assert "document_service_up" in resp.text


class TestSpecCompleteness:
    """Meta-tests: the shared spec must cover every route the live app exposes."""

    def test_live_routes_are_documented(self, openapi_spec: dict[str, Any]) -> None:
        """Every (path, method) in the app's auto-generated /openapi.json is in the spec.

        FastAPI omits routes registered with include_in_schema=False (the
        no-trailing-slash aliases), so hidden routes are excluded by design.
        """
        resp = requests.get(f"{BASE_URL}/openapi.json")
        assert resp.status_code == 200
        live_paths = resp.json()["paths"]
        documented = openapi_spec["paths"]

        http_methods = {"get", "post", "put", "patch", "delete", "head", "options"}
        missing = sorted(
            f"{method.upper()} {path}"
            for path, item in live_paths.items()
            for method in item
            if method in http_methods and method not in documented.get(path, {})
        )
        assert not missing, f"Routes implemented but missing from shared spec: {missing}"

    def test_documented_routes_exist(self, openapi_spec: dict[str, Any]) -> None:
        """Every operation in the shared spec exists in the live app (no stale paths)."""
        live_paths = requests.get(f"{BASE_URL}/openapi.json").json()["paths"]
        http_methods = {"get", "post", "put", "patch", "delete", "head", "options"}
        stale = sorted(
            f"{method.upper()} {path}"
            for path, item in openapi_spec["paths"].items()
            for method in item
            if method in http_methods and method not in live_paths.get(path, {})
        )
        assert not stale, f"Documented in shared spec but not implemented: {stale}"

    def test_spec_schemas_defined(self, openapi_spec: dict[str, Any]) -> None:
        schemas = set(openapi_spec.get("components", {}).get("schemas", {}).keys())
        expected = {
            "DocumentCreate",
            "DocumentUpdate",
            "DocumentPatch",
            "DocumentResponse",
            "DocumentListResponse",
            "DocumentVersionResponse",
            "ShareLinkResponse",
            "CommentCreate",
            "CommentResponse",
            "TemplateCreate",
            "TemplateResponse",
            "DocumentFromTemplate",
            "HealthResponse",
            "ErrorResponse",
            "ValidationError",
        }
        missing = expected - schemas
        assert not missing, f"Missing schemas in spec: {missing}"
