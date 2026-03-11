"""GET /api/metrics — Prometheus exposition format.
GET /metrics — Visual metrics dashboard.
DELETE /api/metrics — Reset all metrics (admin).
"""

from typing import Optional

from fastapi import APIRouter, Header
from fastapi.responses import PlainTextResponse, JSONResponse, HTMLResponse

from infra.cache import get_redis
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
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": "Missing authorization"},
        )

    session_token = authorization[7:].strip()
    if not session_token:
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": "Missing authorization"},
        )

    is_valid = await state.admin_auth.validate_session(session_token)
    if not is_valid:
        return JSONResponse(
            status_code=401,
            content={"ok": False, "error": "Invalid admin session"},
        )

    redis = get_redis()
    await reset_all_metrics(redis)
    return {"ok": True, "message": "All metrics reset"}
