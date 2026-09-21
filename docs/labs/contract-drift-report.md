# Contract drift report — shared/openapi vs. HTTP implementations

Companion to `contract-audit-guide.md`. Each row is a change made to a hand-maintained spec
under `shared/openapi/` so it matches the code, or an explicit decision to leave a documented
drift in place. Planted lab behaviour (golden-app policy, `AGENTS.md`) is left untouched.

## document-service (`shared/openapi/document-service.yaml`)

Source of truth: `services/document-service/app/api/{documents,comments,templates,health}.py`,
`app/schemas/document.py`.

| Operation | Before | After | Why |
|---|---|---|---|
| `GET /api/v1/documents/` | `owner_id`, `page`, `page_size` only; responses 200 | + `title`, `content_type`, `sort`, `direction` query params; + 400, 422 | `list_documents` accepts these filters and returns 400 for an unknown sort column/direction |
| `GET /api/v1/documents/exports` | undocumented | 200 `text/plain`, 404 `Export not found`, 422 (`name` required, `minLength: 1`) | `read_export` |
| `GET /api/v1/documents/shared` | undocumented | 200 `DocumentResponse`, 403 `Invalid share token`, 404 `Document not found`, 422 (`document_id` uuid + `token` required) | `get_shared_document` |
| `GET /api/v1/documents/{document_id}/export` | 200/401/403/404 | + 422 | `format` is constrained by `^(pdf\|html\|markdown)$`; invalid values yield 422 |
| `POST /api/v1/documents/{document_id}/share` | undocumented | 200 `ShareLinkResponse`, 401, 403, 404 | `create_share_link`; new schema `ShareLinkResponse { document_id: uuid, token: string }` (both required) |
| `POST /api/v1/documents` (no trailing slash) | undocumented | **still undocumented** | Registered with `include_in_schema=False`; intentionally hidden, and FastAPI omits it from `/openapi.json` so the completeness test does not flag it |
| comments / templates / from-template / health / metrics | documented | unchanged | Verified against `comments.py`, `templates.py`, `health.py`: paths, methods and status codes match |

## search-service (`shared/openapi/search-service.yaml`)

Source of truth: `services/search-service/app/api/*.py`, `app/models/search_result.py`.

| Item | Decision |
|---|---|
| `DELETE /index/document/{id}` and `DELETE /index/file/{id}` vs. one `<doc_type>/<doc_id>` handler | Kept both concrete paths (they are the only valid instantiations of the template; the handler 400s on any other `doc_type`). Rationale recorded as a YAML comment above the paths; `test_search_contract.py`'s path allowlist expects both. |
| `SuggestResponse.suggestions` is `string[]`, but `chaos:search-service:suggest_500` returns objects / 500 | **Not changed.** Deliberate chaos behaviour; a YAML comment on the schema says why it must not be widened. |

## notification-service (`shared/openapi/notification-service.yaml`)

Source of truth: `services/notification-service/src/main/kotlin/com/otterworks/notification/routes/Routes.kt`,
`model/NotificationEvent.kt`, `Application.kt`.

| Operation | Before | After | Why |
|---|---|---|---|
| `/api/v1/notifications`, `/unread-count`, `/{id}`, `/{id}/read`, `/read-all`, `/api/v1/preferences`, `/health`, `/metrics`, `/ws/notifications/{userId}` | documented | unchanged | Paths, methods, camelCase fields (`userId`, `pageSize`, `hasMore`, `unreadCount`, `markedCount`, `createdAt`, `deliveredVia`) and status codes match the Kotlin data classes and routes |
| `PUT /api/v1/preferences` | 204, 400 | + 500 | A malformed/missing body makes `call.receive()` throw; the global `StatusPages` handler maps every `Throwable` to 500 `{ "error": "Internal server error" }`, not 400. Documented as observed behaviour rather than changed in code. |

## Tests

- `tests/contract/_openapi_utils.py` — shared spec loading, `$ref` resolution, `nullable`
  translation and `jsonschema` validation helpers (extracted from `test_search_contract.py`).
- `tests/contract/test_document_contract.py` — 53 tests including `TestSpecCompleteness`,
  which diffs the live FastAPI `/openapi.json` route list against the shared spec.
- `tests/contract/test_notification_contract.py` — list / unread-count / get / mark-read /
  mark-all-read / preferences / health, success + 400/404/500 paths.
- `make test-contract` (all three, skipping unreachable services) and
  `make test-contract-collect`.
