from fastapi import APIRouter, Cookie, Query

from infra.errors import Err, error_response
from infra.log import logger
import services.state as state

router = APIRouter()


async def _require_admin(admin_session: str | None):
    if not await state.admin_auth.validate_session(admin_session):
        logger.warning("Admin auth rejected: missing or invalid session cookie")
        return error_response(Err.AUTH_UNAUTHORIZED)
    return None


@router.get("/api/admin/tokens")
async def list_tokens(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = Query(""),
    sort_by: str = Query("created_at", pattern="^(created_at|bot_online)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    bot_status: str = Query("", pattern="^(|online)$"),
    admin_session: str | None = Cookie(default=None),
):
    denied = await _require_admin(admin_session)
    if denied:
        return denied
    data = await state.token_manager.list_all(
        page=1, page_size=10000, search=search
    )
    connections = await state.bridge.get_connections_summary()

    # Build full list with connection info
    all_tokens = []
    for t in data["items"]:
        conn = connections.get(t["token"], {})
        all_tokens.append({
            "token": t["token"],
            "name": t.get("name", ""),
            "created_at": t["created_at"],
            "expires_at": t["expires_at"],
            "bot_online": conn.get("bot_online", False),
        })

    # Global stats (across ALL tokens, before filtering)
    global_online = sum(1 for t in all_tokens if t["bot_online"])

    # Filter by bot status
    filtered = all_tokens
    if bot_status == "online":
        filtered = [t for t in filtered if t["bot_online"]]

    # Sort
    reverse = sort_order == "desc"
    if sort_by == "bot_online":
        filtered.sort(key=lambda t: (t["bot_online"], t["created_at"]), reverse=reverse)
    else:
        filtered.sort(key=lambda t: t["created_at"], reverse=reverse)

    # Paginate
    total = len(filtered)
    offset = (page - 1) * page_size
    page_items = filtered[offset:offset + page_size]

    return {
        "code": 0,
        "tokens": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "online_bots": global_online,
        "total_tokens": len(all_tokens),
    }


@router.post("/api/admin/tokens")
async def admin_create_token(body: dict = {}, admin_session: str | None = Cookie(default=None)):
    denied = await _require_admin(admin_session)
    if denied:
        return denied
    name = body.get("name", "")
    expires_in = body.get("expires_in", 86400)
    token = await state.token_manager.generate(name=name, expires_in=expires_in)
    logger.info("Admin created token: {}... (name={})", token[:16], name)
    return {"code": 0, "token": token}


@router.delete("/api/admin/tokens/{token_value}")
async def admin_delete_token(token_value: str, admin_session: str | None = Cookie(default=None)):
    denied = await _require_admin(admin_session)
    if denied:
        return denied
    await state.token_manager.remove(token_value)
    await state.bridge.remove_bot_sessions(token_value)
    logger.info("Admin deleted token: {}...", token_value[:16])
    return {"code": 0}


@router.patch("/api/admin/tokens/{token_value}")
async def admin_update_token(token_value: str, body: dict, admin_session: str | None = Cookie(default=None)):
    denied = await _require_admin(admin_session)
    if denied:
        return denied
    name = body.get("name")
    expires_in = body.get("expires_in")
    if not await state.token_manager.update(token_value, name=name, expires_in=expires_in):
        return error_response(Err.TOKEN_NOT_FOUND)
    logger.info("Admin updated token: {}...", token_value[:16])
    return {"code": 0}


@router.post("/api/admin/cleanup")
async def admin_cleanup(admin_session: str | None = Cookie(default=None)):
    denied = await _require_admin(admin_session)
    if denied:
        return denied
    token_count = await state.token_manager.cleanup_expired()
    session_count = await state.bridge.cleanup_old_sessions(max_age_days=30)
    logger.info("Admin cleanup: removed {} tokens, {} sessions", token_count, session_count)
    return {"code": 0, "removed_tokens": token_count, "removed_sessions": session_count}
