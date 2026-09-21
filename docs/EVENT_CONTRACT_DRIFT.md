# Event contract drift reconciliation

Record of each discrepancy between `shared/events/schemas/*.json` and the payloads the
services actually emit, and how it was reconciled. In every case the **schema** was changed to
match the emitter; no service code was touched (golden-app policy, see `AGENTS.md`). The
reconciled state is enforced by `tests/contract/test_event_contracts.py`
(`make test-contract-events`).

## file-events.json  (producer: `services/file-service/src/events.rs`)

| # | Before (schema) | After (schema) | Evidence |
|---|-----------------|----------------|----------|
| F1 | Only `file_uploaded`, `file_shared`, `file_deleted` defined | Added `FileTrashedEvent`, `FileRestoredEvent`, `FileUpdatedEvent`, `FileMovedEvent` | `fn file_trashed/file_restored/file_updated/file_moved` |
| F2 | `FileUploadedEvent.fileName` (required) | `name` (required) | `FileEvent.name`, `#[serde(rename_all = "camelCase")]` |
| F3 | `FileUploadedEvent.s3Key` | Removed | no such field on `FileEvent` |
| F4 | `FileSharedEvent.permission` (required, enum) | Removed | `file_shared()` only sets `shared_with` → `sharedWithUserId` |
| F5 | `FileDeletedEvent.deletedBy` (required) | `ownerId` (required) | `file_deleted(file_id, owner_id)` |
| F6 | `folderId` absent from shared/deleted; `sharedWithUserId` absent from non-share events | Both allowed as `string \| null` on every event (serde emits `null` for `None`) | struct has no `skip_serializing_if` on these two fields |
| F7 | `mimeType`/`sizeBytes` required on upload only | `name`/`mimeType`/`sizeBytes` required on `file_uploaded`, `file_restored`, `file_updated`; omitted elsewhere | `skip_serializing_if = "Option::is_none"` |
| F8 | — | `additionalProperties: false` via shared `FileEventBase` so renamed/unknown fields fail validation | — |

## document-events.json  (producer: `services/document-service/app/services/event_publisher.py`, `document_service.py`)

| # | Before (schema) | After (schema) | Evidence |
|---|-----------------|----------------|----------|
| D1 | Flat camelCase objects with `eventType` | Envelope `{event_type, timestamp, payload}` (snake_case) | `EventPublisher.publish()` builds `message = {event_type, timestamp, payload}` |
| D2 | `DocumentEditedEvent` / `document_edited` | `DocumentUpdatedEvent` / `document_updated` | `publish("document_updated", …)` on update, patch and restore; `document_edited` never published |
| D3 | No delete event | `DocumentDeletedEvent`, payload `{id, type: "document"}` | `delete()` |
| D4 | `DocumentCreated`: `documentId`, `title`, `ownerId`, `folderId`, `contentType` | payload `{id, title, content, owner_id, tags, created_at, updated_at}` (+ optional `restored_from` on restore) | `_document_index_payload()` |
| D5 | `CommentAdded`: `documentId`, `commentId`, `authorId`, `content`, `mentionedUserIds` | payload `{comment_id, document_id, author_id}`; `content`/`mentioned_user_ids` dropped (never emitted) | `add_comment()` |

## collaboration-events.json  (`services/collab-service/src/handlers/collaboration.ts`)

| # | Before | After | Evidence |
|---|--------|-------|----------|
| C1 | Titled as SNS "Collaboration Events" with `eventType` consts `user_joined`, `user_left`, `cursor_moved`, `selection_changed` and `sessionId`/`documentId`/`position{line,column}` | Retitled **Collaboration WebSocket Messages**; definitions describe the Socket.IO payloads for `user-joined`, `user-left`, `cursor-update`, `typing-indicator` (fields `socketId`, `userId`, `displayName`, `color`, `cursor`/`selection` as `{index,length} \| null`, `isTyping`). Event name recorded via `x-socketio-event`. Description states there is **no SNS producer**. | `socket.to(room).emit(...)`; no SNS client in collab-service |

## audit-events.json  (`services/audit-service/src/Services/SnsConsumer.cs`)

| # | Before | After | Evidence |
|---|--------|-------|----------|
| A1 | Implied audit-service produces `entity_*`/`access_*` events | Description states audit-service is a **consumer**; added `AuditEventMessage` (the generic inbound shape actually deserialized: `userId`, `action`, `resourceType`, `resourceId`, `details`, `ipAddress`, `userAgent`, `timestamp`, all optional). `Entity*`/`Access*` kept unchanged as unproduced inbound contracts. | `SnsConsumer.ProcessMessageAsync`, `AuditEventMessage`, `FileEventMessage` |

The lab guide's "Audit Service — Missing Required Field" (`timestamp` required vs. batch
processing) is left as-is for the lab; `timestamp` remains required on `Entity*`/`Access*`.

## notification-events.json  (`services/notification-service/.../model/NotificationEvent.kt`, `consumer/SqsConsumer.kt`)

| # | Before | After | Evidence |
|---|--------|-------|----------|
| N1 | Implied notification-service produces `notification_sent/read/failed` | Description states notification-service is a **consumer** with no publisher; added `SqsNotificationMessage` (inbound camelCase shape: `eventType` ∈ {file_shared, comment_added, document_edited, user_mentioned}, `fileId`, `ownerId`, `sharedWithUserId`, `documentId`, `commentId`, `userId`, `actorId`, `mentionedUserId`, `timestamp`). `NotificationSent/Read/Failed` kept unchanged as unproduced outbound shapes. | `SqsConsumer.parseMessage`, `data class SqsNotificationMessage` |

Note: `docs/labs/contract-audit-guide.md` says notification-service "publishes snake_case".
Inspection found no publisher at all, and the inbound model is camelCase; the schema fields
were therefore **not** renamed to snake_case. The real cross-service mismatch is that
document-service emits a snake_case envelope (`comment_added`) that `SqsNotificationMessage`
cannot decode — this is recorded in the schema description and left for the lab.
