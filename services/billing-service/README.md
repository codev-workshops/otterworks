# Billing Service

This FastAPI service is the extraction target for the plans module. It owns a
separate Postgres `billing_svc` schema, keeps the HTTP layer thin, and places
plans behavior in a plain-Python domain layer.

## Development

```bash
uv sync
uv run uvicorn app.main:app --reload --port 8097
uv run pytest
uv run ruff check app scripts tests
```

The deterministic target seed is generated from
`services/legacy-billing/db/seed.sql`:

```bash
python scripts/generate_seed.py
```

The generated-seed test prevents the target fixture from drifting from the
legacy before-state. `POST /internal/reset` applies the migration, truncates
the `billing_svc` schema, and reseeds it so the parity harness can isolate
every scenario.

The reset endpoint is disabled by default. Disposable local/CI Compose stacks
enable it with `BILLING_SVC_ALLOW_INTERNAL_RESET=true`; published deployments
should leave the setting disabled.

The HTTP endpoints are intentionally unauthenticated in this parity fixture.
Authentication and tenant scoping are out of scope here; an extraction that
ships for real must add both at the edge before exposing these endpoints.

For the extracted target, a plan change with an already-scheduled later
subscription preserves that later row. The response's `latest_*` fields always
identify the subscription created by the request, rather than relying on row
ordering.

The legacy procedure attempts a second insert for an identical plan change and
therefore relies on the database uniqueness error. The extracted target returns
HTTP 409 with an explicit conflict detail instead of leaking a 500; this is
target-side error handling, not an additional parity rule.

When using the workshop client with the default disposable stack, the Vite
development proxy forwards `/billing-api/*` to the service on port `12109`.
The billing screens are part of this local parity fixture only. Vite dev
enables their routes by default; a preview requires building with
`VITE_ENABLE_BILLING_FIXTURE=true` and then running `npm run start`. The
`/billing-api` proxy is used by the dev server and by that explicitly flagged
preview. Builds without the flag leave the routes unregistered. No deployed
app or shared-infrastructure deployment is provided for this fixture.

## Observability

- Logs are JSON on stdout (`service`, `request_id`, `trace_id`, `span_id`); set
  `LOG_FORMAT=text` for a console format.
- Every response echoes `X-Request-ID` (a valid inbound value is reused, otherwise a UUID
  is generated) and the ID is set on the server span as `request.id`.
- `GET /metrics` serves Prometheus text: `http_requests_total`,
  `http_request_duration_seconds`, `billing_plan_changes_total{outcome}`,
  `billing_plan_change_duration_seconds{outcome}` and
  `billing_entitlement_lookups_total{outcome}`.
- FastAPI and psycopg spans are exported over OTLP/HTTP when
  `OTEL_EXPORTER_OTLP_ENDPOINT` (or `..._TRACES_ENDPOINT`) is set; `TRACING_ENABLED=false`
  turns tracing off.
- `GET /health` runs `SELECT 1` and returns 503 when Postgres is unreachable.
