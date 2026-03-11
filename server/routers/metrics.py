"""GET /api/metrics — Prometheus exposition format.
GET /metrics — Visual metrics dashboard.
DELETE /api/metrics — Reset all metrics (admin).
"""

from typing import Optional

from fastapi import APIRouter, Header
from fastapi.responses import PlainTextResponse, HTMLResponse

from infra.cache import get_redis
from infra.errors import Err, error_response
from infra.log import logger
from infra.telemetry.reader import render_prometheus_exposition, reset_all_metrics
import services.state as state

router = APIRouter()

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get("/metrics", response_class=HTMLResponse)
async def serve_metrics_dashboard():
    """Serve the metrics visualisation page."""
    html_file = state.frontend_dir / "metrics.html"
    if html_file.is_file():
        return HTMLResponse(content=html_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Metrics</h1><p>Dashboard not found.</p>")


@router.get("/api/metrics")
async def get_metrics():
    """Prometheus scrape endpoint — returns exposition format text."""
    redis = get_redis()
    text = await render_prometheus_exposition(redis)
    return PlainTextResponse(content=text, media_type=PROMETHEUS_CONTENT_TYPE)


@router.delete("/api/metrics")
async def delete_metrics(
    authorization: Optional[str] = Header(default=None),
):
    """Reset all OTLP metrics. Requires admin auth."""
    # Reuse admin session validation
    if not authorization or not authorization.lower().startswith("bearer "):
        logger.warning("Metrics reset rejected: invalid authorization")
        return error_response(Err.AUTH_MISSING_AUTH)

    session_token = authorization[7:].strip()
    if not session_token:
        logger.warning("Metrics reset rejected: invalid authorization")
        return error_response(Err.AUTH_MISSING_AUTH)

    is_valid = await state.admin_auth.validate_session(session_token)
    if not is_valid:
        logger.warning("Metrics reset rejected: invalid admin session")
        return error_response(Err.AUTH_INVALID_SESSION)

    redis = get_redis()
    await reset_all_metrics(redis)
    logger.info("Metrics reset by admin")
    return {"code": 0, "message": "All metrics reset"}
