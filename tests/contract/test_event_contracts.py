"""Contract tests for the SNS event schemas under shared/events/schemas/.

Validates representative event payloads -- captured from the shapes each
producer actually serializes -- against the JSON Schema (draft-07) contracts,
and checks that every event type a producer emits has a matching definition.

Producers covered:
  - file-service      services/file-service/src/events.rs        (flat camelCase)
  - document-service  services/document-service/app/services/    (snake_case envelope)

Static fixtures are used so the suite runs in CI without SNS/localstack.
Set EVENT_CONTRACT_SAMPLES_DIR to a directory of captured `*.json` messages
(one event per file) to additionally validate live-emitted payloads.

Usage:
    pytest tests/contract/test_event_contracts.py -v

Requirements:
    pip install jsonschema pytest
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft7Validator, FormatChecker, ValidationError

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "shared" / "events" / "schemas"
SAMPLES_DIR = os.environ.get("EVENT_CONTRACT_SAMPLES_DIR")

SCHEMA_FILES = {
    "file": "file-events.json",
    "document": "document-events.json",
    "collaboration": "collaboration-events.json",
    "audit": "audit-events.json",
    "notification": "notification-events.json",
}

# Event types each SNS producer emits, keyed by the schema definition that
# describes them. Mirrors the `event_type` literals in the emitter source.
FILE_SERVICE_EVENTS = {
    "file_uploaded": "FileUploadedEvent",
    "file_deleted": "FileDeletedEvent",
    "file_shared": "FileSharedEvent",
    "file_trashed": "FileTrashedEvent",
    "file_restored": "FileRestoredEvent",
    "file_updated": "FileUpdatedEvent",
    "file_moved": "FileMovedEvent",
}

DOCUMENT_SERVICE_EVENTS = {
    "document_created": "DocumentCreatedEvent",
    "document_updated": "DocumentUpdatedEvent",
    "document_deleted": "DocumentDeletedEvent",
    "comment_added": "CommentAddedEvent",
}

# Socket.IO event names broadcast by collab-service, keyed by definition.
COLLAB_SOCKET_EVENTS = {
    "user-joined": "UserJoinedMessage",
    "user-left": "UserLeftMessage",
    "cursor-update": "CursorUpdateMessage",
    "typing-indicator": "TypingIndicatorMessage",
}

FILE_ID = "6f1c2a3e-8b4d-4c5e-9f60-1a2b3c4d5e6f"
OWNER_ID = "0b9a8c7d-6e5f-4a3b-2c1d-0e9f8a7b6c5d"
FOLDER_ID = "aa11bb22-cc33-4d44-8e55-ff6677889900"
OTHER_USER_ID = "12345678-1234-4123-8123-123456789abc"
DOC_ID = "9e8d7c6b-5a49-4382-9716-05f4e3d2c1b0"
COMMENT_ID = "c0ffee00-1234-4abc-8def-0123456789ab"
VERSION_ID = "deadbeef-0000-4000-8000-000000000001"
TS = "2026-09-21T08:32:11.123456+00:00"


def _file_event(event_type: str, **extra: Any) -> dict[str, Any]:
    """Serialized FileEvent: folderId/sharedWithUserId always present (null when unset)."""
    event = {
        "eventType": event_type,
        "fileId": FILE_ID,
        "ownerId": OWNER_ID,
        "folderId": None,
        "sharedWithUserId": None,
        "timestamp": TS,
    }
    event.update(extra)
    return event


FILE_METADATA = {"name": "q3-report.pdf", "mimeType": "application/pdf", "sizeBytes": 204800}

FILE_SERVICE_SAMPLES: dict[str, dict[str, Any]] = {
    "file_uploaded": _file_event("file_uploaded", folderId=FOLDER_ID, **FILE_METADATA),
    "file_deleted": _file_event("file_deleted"),
    "file_shared": _file_event("file_shared", sharedWithUserId=OTHER_USER_ID),
    "file_trashed": _file_event("file_trashed"),
    "file_restored": _file_event("file_restored", folderId=FOLDER_ID, **FILE_METADATA),
    "file_updated": _file_event("file_updated", **FILE_METADATA),
    "file_moved": _file_event("file_moved", folderId=FOLDER_ID),
}


def _envelope(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"event_type": event_type, "timestamp": TS, "payload": payload}


DOCUMENT_INDEX_PAYLOAD = {
    "id": DOC_ID,
    "title": "Roadmap",
    "content": "# Roadmap\n\nQ4 goals",
    "owner_id": OWNER_ID,
    "tags": [],
    "created_at": TS,
    "updated_at": TS,
}

DOCUMENT_SERVICE_SAMPLES: dict[str, dict[str, Any]] = {
    "document_created": _envelope("document_created", DOCUMENT_INDEX_PAYLOAD),
    "document_updated": _envelope("document_updated", DOCUMENT_INDEX_PAYLOAD),
    "document_deleted": _envelope("document_deleted", {"id": DOC_ID, "type": "document"}),
    "comment_added": _envelope(
        "comment_added",
        {"comment_id": COMMENT_ID, "document_id": DOC_ID, "author_id": OTHER_USER_ID},
    ),
}

DOCUMENT_RESTORED_SAMPLE = _envelope(
    "document_updated", {**DOCUMENT_INDEX_PAYLOAD, "restored_from": VERSION_ID}
)

COLLAB_SAMPLES: dict[str, dict[str, Any]] = {
    "user-joined": {
        "userId": "user-001",
        "displayName": "Ada",
        "color": "#FF6B6B",
        "socketId": "Xk2p9QzL8mN3vR4tAAAB",
    },
    "user-left": {"socketId": "Xk2p9QzL8mN3vR4tAAAB", "userId": "user-001"},
    "cursor-update": {
        "socketId": "Xk2p9QzL8mN3vR4tAAAB",
        "userId": "user-001",
        "displayName": "Ada",
        "color": "#FF6B6B",
        "cursor": {"index": 42, "length": 0},
        "selection": None,
    },
    "typing-indicator": {
        "socketId": "Xk2p9QzL8mN3vR4tAAAB",
        "userId": "user-001",
        "displayName": "Ada",
        "isTyping": True,
    },
}


@pytest.fixture(scope="session")
def schemas() -> dict[str, dict[str, Any]]:
    """Load every JSON Schema under shared/events/schemas/, keyed by short name."""
    loaded: dict[str, dict[str, Any]] = {}
    for name, filename in SCHEMA_FILES.items():
        with open(SCHEMA_DIR / filename) as f:
            loaded[name] = json.load(f)
    return loaded


def _validator(schema: dict[str, Any], definition: str) -> Draft7Validator:
    """Build a validator for one `definitions` entry, keeping in-document $refs resolvable."""
    assert definition in schema["definitions"], f"No definition {definition!r} in {schema['title']}"
    root = {**schema, "$ref": f"#/definitions/{definition}"}
    return Draft7Validator(root, format_checker=FormatChecker())


def _validate(schema: dict[str, Any], definition: str, instance: Any) -> None:
    errors = sorted(_validator(schema, definition).iter_errors(instance), key=str)
    assert not errors, f"{definition} rejected payload:\n" + "\n".join(
        f"  - {'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors
    )


def _event_types_in_schema(schema: dict[str, Any], field: str) -> set[str]:
    """Collect every `const` value of `field` declared by a definition (directly or via allOf)."""
    found: set[str] = set()
    for definition in schema["definitions"].values():
        branches = [definition, *definition.get("allOf", [])]
        for branch in branches:
            prop = branch.get("properties", {}).get(field, {})
            if "const" in prop:
                found.add(prop["const"])
    return found


class TestSchemaValidity:
    """Every schema file must itself be valid JSON Schema draft-07."""

    @pytest.mark.parametrize("name", sorted(SCHEMA_FILES))
    def test_schema_is_valid_draft7(self, schemas: dict[str, dict[str, Any]], name: str) -> None:
        schema = schemas[name]
        assert schema["$schema"] == "http://json-schema.org/draft-07/schema#"
        Draft7Validator.check_schema(schema)
        assert schema.get("definitions"), f"{name}: expected non-empty definitions"


class TestFileServiceEvents:
    """file-service SNS payloads (flat camelCase FileEvent)."""

    @pytest.mark.parametrize("event_type", sorted(FILE_SERVICE_EVENTS))
    def test_sample_validates(self, schemas: dict[str, dict[str, Any]], event_type: str) -> None:
        _validate(
            schemas["file"], FILE_SERVICE_EVENTS[event_type], FILE_SERVICE_SAMPLES[event_type]
        )

    def test_uploaded_without_folder(self, schemas: dict[str, dict[str, Any]]) -> None:
        """Root-level uploads carry folderId: null."""
        sample = _file_event("file_uploaded", **FILE_METADATA)
        _validate(schemas["file"], "FileUploadedEvent", sample)

    def test_legacy_field_names_rejected(self, schemas: dict[str, dict[str, Any]]) -> None:
        """The pre-reconciliation contract (fileName/s3Key/deletedBy/permission) must not validate."""
        legacy_upload = {**FILE_SERVICE_SAMPLES["file_uploaded"], "fileName": "x", "s3Key": "k"}
        del legacy_upload["name"]
        with pytest.raises(AssertionError):
            _validate(schemas["file"], "FileUploadedEvent", legacy_upload)

        legacy_delete = {**FILE_SERVICE_SAMPLES["file_deleted"], "deletedBy": OWNER_ID}
        with pytest.raises(AssertionError):
            _validate(schemas["file"], "FileDeletedEvent", legacy_delete)

    def test_shared_requires_recipient(self, schemas: dict[str, dict[str, Any]]) -> None:
        sample = _file_event("file_shared")
        with pytest.raises(AssertionError):
            _validate(schemas["file"], "FileSharedEvent", sample)

    def test_event_type_mismatch_rejected(self, schemas: dict[str, dict[str, Any]]) -> None:
        with pytest.raises(AssertionError):
            _validate(schemas["file"], "FileTrashedEvent", FILE_SERVICE_SAMPLES["file_deleted"])


class TestDocumentServiceEvents:
    """document-service SNS payloads ({event_type, timestamp, payload} envelope)."""

    @pytest.mark.parametrize("event_type", sorted(DOCUMENT_SERVICE_EVENTS))
    def test_sample_validates(self, schemas: dict[str, dict[str, Any]], event_type: str) -> None:
        _validate(
            schemas["document"],
            DOCUMENT_SERVICE_EVENTS[event_type],
            DOCUMENT_SERVICE_SAMPLES[event_type],
        )

    def test_restore_emits_updated_with_restored_from(
        self, schemas: dict[str, dict[str, Any]]
    ) -> None:
        _validate(schemas["document"], "DocumentUpdatedEvent", DOCUMENT_RESTORED_SAMPLE)

    def test_envelope_required(self, schemas: dict[str, dict[str, Any]]) -> None:
        """A flat (un-enveloped) payload must be rejected."""
        flat = {"event_type": "document_created", "timestamp": TS, **DOCUMENT_INDEX_PAYLOAD}
        with pytest.raises(AssertionError):
            _validate(schemas["document"], "DocumentCreatedEvent", flat)

    def test_legacy_camel_case_rejected(self, schemas: dict[str, dict[str, Any]]) -> None:
        legacy = {
            "eventType": "document_created",
            "documentId": DOC_ID,
            "title": "Roadmap",
            "ownerId": OWNER_ID,
            "timestamp": TS,
        }
        with pytest.raises(AssertionError):
            _validate(schemas["document"], "DocumentCreatedEvent", legacy)

    def test_document_edited_not_defined(self, schemas: dict[str, dict[str, Any]]) -> None:
        """The service publishes document_updated; the old document_edited name is gone."""
        assert "DocumentEditedEvent" not in schemas["document"]["definitions"]
        assert "document_edited" not in _event_types_in_schema(schemas["document"], "event_type")


class TestCollaborationMessages:
    """collab-service Socket.IO payloads (not SNS)."""

    @pytest.mark.parametrize("socket_event", sorted(COLLAB_SOCKET_EVENTS))
    def test_sample_validates(self, schemas: dict[str, dict[str, Any]], socket_event: str) -> None:
        _validate(
            schemas["collaboration"],
            COLLAB_SOCKET_EVENTS[socket_event],
            COLLAB_SAMPLES[socket_event],
        )

    def test_definitions_record_socketio_event_name(
        self, schemas: dict[str, dict[str, Any]]
    ) -> None:
        recorded = {
            d["x-socketio-event"]: name
            for name, d in schemas["collaboration"]["definitions"].items()
            if "x-socketio-event" in d
        }
        assert recorded == COLLAB_SOCKET_EVENTS


class TestConsumerOnlySchemas:
    """audit/notification schemas describe inbound shapes; make sure that is stated."""

    @pytest.mark.parametrize("name", ["audit", "notification", "collaboration"])
    def test_producer_status_documented(
        self, schemas: dict[str, dict[str, Any]], name: str
    ) -> None:
        description = schemas[name].get("description", "").lower()
        assert "consumer" in description or "not sns" in description, (
            f"{name}: description must state that no SNS producer exists"
        )

    def test_audit_consumer_accepts_file_shared(self, schemas: dict[str, dict[str, Any]]) -> None:
        """SnsConsumer.cs handles file-service file_shared events from the file schema."""
        _validate(schemas["file"], "FileSharedEvent", FILE_SERVICE_SAMPLES["file_shared"])

    def test_notification_inbound_message(self, schemas: dict[str, dict[str, Any]]) -> None:
        _validate(
            schemas["notification"],
            "SqsNotificationMessage",
            {
                "eventType": "file_shared",
                "fileId": FILE_ID,
                "ownerId": OWNER_ID,
                "sharedWithUserId": OTHER_USER_ID,
                "timestamp": TS,
            },
        )


class TestSchemaCompleteness:
    """Meta-tests: producers and schemas must agree on the set of event types."""

    def test_file_schema_covers_all_emitted_events(
        self, schemas: dict[str, dict[str, Any]]
    ) -> None:
        declared = _event_types_in_schema(schemas["file"], "eventType")
        emitted = set(FILE_SERVICE_EVENTS)
        assert not emitted - declared, (
            f"file-service emits undocumented events: {emitted - declared}"
        )
        assert not declared - emitted, (
            f"file-events.json defines unemitted events: {declared - emitted}"
        )
        assert (
            set(schemas["file"]["definitions"]["FileEventBase"]["properties"]["eventType"]["enum"])
            == emitted
        )

    def test_document_schema_covers_all_emitted_events(
        self, schemas: dict[str, dict[str, Any]]
    ) -> None:
        declared = _event_types_in_schema(schemas["document"], "event_type")
        emitted = set(DOCUMENT_SERVICE_EVENTS)
        assert not emitted - declared, (
            f"document-service emits undocumented events: {emitted - declared}"
        )
        assert not declared - emitted, (
            f"document-events.json defines unemitted events: {declared - emitted}"
        )
        assert (
            set(schemas["document"]["definitions"]["Envelope"]["properties"]["event_type"]["enum"])
            == emitted
        )

    @pytest.mark.parametrize(
        ("name", "definitions"),
        [
            ("file", set(FILE_SERVICE_EVENTS.values())),
            ("document", set(DOCUMENT_SERVICE_EVENTS.values())),
            ("collaboration", set(COLLAB_SOCKET_EVENTS.values())),
        ],
    )
    def test_expected_definitions_present(
        self, schemas: dict[str, dict[str, Any]], name: str, definitions: set[str]
    ) -> None:
        missing = definitions - set(schemas[name]["definitions"])
        assert not missing, f"{name}: missing definitions {missing}"

    def test_emitter_sources_reference_every_event_type(self) -> None:
        """Guard the constants above against drift in the emitter source itself."""
        rust = (
            SCHEMA_DIR.parents[2] / "services" / "file-service" / "src" / "events.rs"
        ).read_text()
        for event_type in FILE_SERVICE_EVENTS:
            assert f'"{event_type}".into()' in rust, f"events.rs no longer emits {event_type}"

        py = (
            SCHEMA_DIR.parents[2]
            / "services"
            / "document-service"
            / "app"
            / "services"
            / "document_service.py"
        ).read_text()
        for event_type in DOCUMENT_SERVICE_EVENTS:
            assert f'"{event_type}"' in py, f"document_service.py no longer emits {event_type}"
        assert '"document_edited"' not in py


@pytest.mark.skipif(not SAMPLES_DIR, reason="EVENT_CONTRACT_SAMPLES_DIR not set")
class TestCapturedSamples:
    """Validate captured live messages (one JSON object per file) against the schemas."""

    def test_captured_messages_validate(self, schemas: dict[str, dict[str, Any]]) -> None:
        paths = sorted(Path(SAMPLES_DIR or "").glob("*.json"))
        assert paths, f"No *.json samples in {SAMPLES_DIR}"
        for path in paths:
            with open(path) as f:
                message = json.load(f)
            if "Message" in message and "TopicArn" in message:
                message = json.loads(message["Message"])
            if "eventType" in message:
                definition = FILE_SERVICE_EVENTS[message["eventType"]]
                _validate(schemas["file"], definition, message)
            elif "event_type" in message:
                definition = DOCUMENT_SERVICE_EVENTS[message["event_type"]]
                _validate(schemas["document"], definition, message)
            else:
                raise ValidationError(f"{path.name}: cannot determine producer for message")
