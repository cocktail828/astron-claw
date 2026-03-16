from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from infra.errors import Err
from infra.log import logger
from services.bridge import _BOT_TTL
import services.state as state

router = APIRouter()


@router.websocket("/bridge/bot")
async def ws_bot(
    ws: WebSocket,
    token: str = Query(default=""),
):
    bot_token = token or (ws.headers.get("x-astron-bot-token", ""))
    if not await state.token_manager.validate(bot_token):
        await ws.accept()
        await ws.close(code=Err.WS_INVALID_TOKEN.status, reason=Err.WS_INVALID_TOKEN.message)
        logger.warning("Bot connection rejected: invalid token {}...", bot_token[:10])
        return

    await ws.accept()

    if not await state.bridge.register_bot(bot_token, ws):
        await ws.send_json({
            "error": Err.WS_DUPLICATE_BOT.message,
            "code": Err.WS_DUPLICATE_BOT.status,
            "retry_after": _BOT_TTL,
        })
        await ws.close(code=Err.WS_DUPLICATE_BOT.status, reason=Err.WS_DUPLICATE_BOT.message)
        logger.warning("Bot connection rejected: duplicate token {}...", bot_token[:10])
        return

    logger.info("Bot connected: {}...", bot_token[:10])
    state.bridge.notify_bot_connected(bot_token)
    try:
        while True:
            raw = await ws.receive_text()
            await state.bridge.handle_bot_message(bot_token, raw)
    except WebSocketDisconnect:
        logger.info("Bot disconnected: {}...", bot_token[:10])
    except Exception:
        logger.exception("Bot connection error: {}...", bot_token[:10])
    finally:
        await state.bridge.unregister_bot(bot_token, ws)
