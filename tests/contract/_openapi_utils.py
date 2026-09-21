"""Shared helpers for OpenAPI contract tests.

Loads a spec from shared/openapi/, resolves $ref pointers, and validates live
HTTP response bodies against the documented response schemas.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
import yaml
from jsonschema import validate

OPENAPI_DIR = Path(__file__).resolve().parents[2] / "shared" / "openapi"


def load_spec(name: str) -> dict[str, Any]:
    """Load shared/openapi/<name>.yaml and assert it is OpenAPI 3.0.x."""
    with open(OPENAPI_DIR / f"{name}.yaml") as f:
        spec = yaml.safe_load(f)
    assert spec.get("openapi", "").startswith("3.0"), "Expected OpenAPI 3.0.x spec"
    return spec


def service_base_url(env_var: str, default: str) -> str:
    return os.environ.get(env_var, default).rstrip("/")


def require_live_service(base_url: str, env_var: str) -> None:
    """Skip the calling test module unless the service accepts TCP connections.

    Contract tests need a running stack. When the service is unreachable the
    module is skipped (rather than failing) so ``pytest --collect-only`` and
    CI runs without a live stack stay green.
    """
    parsed = urlparse(base_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=2):
            return
    except OSError:
        pytest.skip(
            f"{env_var}={base_url} is not reachable; start the service or point "
            f"{env_var} at a live instance",
            allow_module_level=True,
        )


def resolve_ref(spec: dict[str, Any], ref: str) -> dict[str, Any]:
    """Resolve a JSON $ref pointer within the spec."""
    parts = ref.lstrip("#/").split("/")
    node = spec
    for part in parts:
        node = node[part]
    return node


def get_response_schema(
    spec: dict[str, Any],
    path: str,
    method: str,
    status_code: str,
    media_type: str = "application/json",
) -> dict[str, Any] | None:
    """Extract the schema for a given path/method/status/media-type response."""
    path_item = spec.get("paths", {}).get(path)
    if not path_item:
        return None
    operation = path_item.get(method)
    if not operation:
        return None
    response = operation.get("responses", {}).get(status_code)
    if not response:
        return None
    content = response.get("content", {}).get(media_type, {})
    schema = content.get("schema")
    if not schema:
        return None
    if "$ref" in schema:
        schema = resolve_ref(spec, schema["$ref"])
    return schema


def resolve_schema_refs(spec: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively resolve all $ref in a schema for validation.

    Also translates OpenAPI 3.0's ``nullable: true`` into the JSON Schema
    equivalent (``type: [<type>, "null"]``), which jsonschema understands.
    """
    if "$ref" in schema:
        return resolve_schema_refs(spec, resolve_ref(spec, schema["$ref"]))

    resolved = dict(schema)

    if resolved.pop("nullable", False) and "type" in resolved:
        types = resolved["type"] if isinstance(resolved["type"], list) else [resolved["type"]]
        if "null" not in types:
            resolved["type"] = [*types, "null"]

    if "properties" in resolved:
        resolved["properties"] = {
            k: resolve_schema_refs(spec, v) for k, v in resolved["properties"].items()
        }

    if "items" in resolved:
        resolved["items"] = resolve_schema_refs(spec, resolved["items"])

    if "additionalProperties" in resolved and isinstance(resolved["additionalProperties"], dict):
        resolved["additionalProperties"] = resolve_schema_refs(
            spec, resolved["additionalProperties"]
        )

    for combinator in ("allOf", "oneOf", "anyOf"):
        if combinator in resolved:
            resolved[combinator] = [resolve_schema_refs(spec, s) for s in resolved[combinator]]

    return resolved


def validate_response(
    spec: dict[str, Any],
    response_json: Any,
    path: str,
    method: str,
    status_code: str,
) -> None:
    """Validate a JSON response body against the spec schema."""
    schema = get_response_schema(spec, path, method, status_code)
    assert schema is not None, f"No schema found for {method.upper()} {path} -> {status_code}"
    validate(instance=response_json, schema=resolve_schema_refs(spec, schema))


def assert_documented_status(
    spec: dict[str, Any], path: str, method: str, status_code: int
) -> None:
    """Assert the spec documents ``status_code`` for the operation."""
    operation = spec.get("paths", {}).get(path, {}).get(method)
    assert operation is not None, f"{method.upper()} {path} is not in the spec"
    documented = set(operation.get("responses", {}).keys())
    assert str(status_code) in documented, (
        f"{method.upper()} {path} returned {status_code}, spec documents {sorted(documented)}"
    )
