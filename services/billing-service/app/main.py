from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated
from uuid import UUID

import psycopg
import structlog
from fastapi import FastAPI, HTTPException, Path, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import settings
from app.db import connect, migrate, reset
from app.domain import catalog, change_plan, entitlement
from app.observability import (
    ENTITLEMENT_LOOKUPS,
    PLAN_CHANGE_SECONDS,
    PLAN_CHANGES,
    configure_logging,
    configure_tracing,
    install_request_middleware,
    metrics_response,
)
from app.repository import PostgresPlansRepository

configure_logging(settings.log_format)
logger = structlog.get_logger("billing")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    migrate()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
install_request_middleware(app)
configure_tracing(app, settings.tracing_enabled)


class PlanChange(BaseModel):
    plan_id: UUID
    effective_on: date


@app.get("/health")
def health() -> dict[str, str]:
    try:
        with connect() as connection:
            connection.execute("SELECT 1")
    except psycopg.Error as error:
        logger.warning("health_check_failed", dependency="postgres", error=type(error).__name__)
        raise HTTPException(status_code=503, detail="database unavailable") from error
    return {"status": "healthy", "service": settings.app_name}


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return metrics_response()


@app.post("/internal/reset", status_code=204)
def internal_reset() -> Response:
    if not settings.allow_internal_reset:
        raise HTTPException(status_code=404, detail="internal reset is disabled")
    reset()
    return Response(status_code=204)


@app.get("/api/plans")
def list_plans() -> list[dict]:
    with connect() as connection:
        plans = catalog(PostgresPlansRepository(connection).list_plans())
    return [
        {
            "plan_id": str(plan.plan_id),
            "code": plan.code,
            "tier": plan.tier,
            "monthly_fee": f"{plan.monthly_fee:.2f}",
            "included_units": plan.included_units,
            "overage_rate": f"{plan.overage_rate:.6f}",
        }
        for plan in plans
    ]


@app.get("/api/tenants/{tenant_id}/entitlement")
def get_entitlement(
    tenant_id: Annotated[UUID, Path()],
    on: Annotated[date, Query()],
) -> dict:
    with connect() as connection:
        row = entitlement(
            PostgresPlansRepository(connection).find_entitlements(tenant_id),
            tenant_id,
            on,
        )
    if row is None:
        ENTITLEMENT_LOOKUPS.labels("not_found").inc()
        logger.info("entitlement_lookup", tenant_id=str(tenant_id), on=on.isoformat(), found=False)
        raise HTTPException(status_code=404, detail="entitlement not found")
    ENTITLEMENT_LOOKUPS.labels("found").inc()
    logger.info(
        "entitlement_lookup",
        tenant_id=str(tenant_id),
        on=on.isoformat(),
        found=True,
        plan_code=row.plan_code,
    )
    return {
        "tenant_id": str(row.tenant_id),
        "plan_code": row.plan_code,
        "tier": row.tier,
        "monthly_fee": f"{row.monthly_fee:.2f}",
        "included_units": row.included_units,
        "subscription_status": row.subscription_status,
        "effective_on": max(row.starts_on, on).isoformat(),
    }


@app.post("/api/tenants/{tenant_id}/plan-change")
def change_tenant_plan(tenant_id: Annotated[UUID, Path()], request: PlanChange) -> dict:
    start = time.perf_counter()
    outcome = "error"
    try:
        with connect() as connection:
            repository = PostgresPlansRepository(connection)
            subscriptions, created = change_plan(
                repository,
                tenant_id,
                request.plan_id,
                request.effective_on,
            )
            outcome = "created"
            return {
                "latest_plan": str(created.plan_id),
                "latest_start": created.starts_on.isoformat(),
                "subscriptions": [
                    {
                        "plan_id": str(item.plan_id),
                        "starts_on": item.starts_on.isoformat(),
                        "ends_on": item.ends_on.isoformat() if item.ends_on else None,
                        "status": item.status,
                    }
                    for item in subscriptions
                ],
            }
    except psycopg.errors.ForeignKeyViolation as error:
        outcome = "invalid"
        raise HTTPException(status_code=400, detail="invalid plan change") from error
    except psycopg.errors.UniqueViolation as error:
        outcome = "conflict"
        raise HTTPException(
            status_code=409,
            detail="this plan change has already been requested",
        ) from error
    finally:
        PLAN_CHANGES.labels(outcome).inc()
        PLAN_CHANGE_SECONDS.labels(outcome).observe(time.perf_counter() - start)
        logger.info(
            "plan_change",
            tenant_id=str(tenant_id),
            plan_id=str(request.plan_id),
            effective_on=request.effective_on.isoformat(),
            outcome=outcome,
        )
