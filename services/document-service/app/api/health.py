"""Health check and metrics endpoints."""

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

logger = structlog.get_logger()
router = APIRouter()

SERVICE_UP = Gauge("document_service_up", "Document Service is running")
SERVICE_UP.set(1)


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    """Health check with DB connectivity verification; 503 when the database is down."""
    try:
        await db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        logger.exception("health_check_db_failed")
        db_status = "disconnected"

    healthy = db_status == "connected"
    return JSONResponse(
        {
            "status": "healthy" if healthy else "degraded",
            "service": "document-service",
            "checks": {"database": db_status},
        },
        status_code=200 if healthy else 503,
    )


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    """Prometheus metrics endpoint."""
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)
