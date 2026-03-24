from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from infra.errors import Err
from infra.log import logger
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

    if state.bridge.is_draining():
        await ws.accept()
        await ws.close(code=Err.WS_SERVER_RESTART.status, reason=Err.WS_SERVER_RESTART.message)
        logger.info("Bot connection rejected: worker draining token={}...", bot_token[:10])
        return

    await ws.accept()

    client = ws.client
    client_addr = f"{client.host}:{client.port}" if client else "unknown"

    await state.bridge.register_bot(bot_token, ws)

    logger.info("Bot connected: {}... from={}", bot_token[:10], client_addr)
    state.bridge.notify_bot_connected(bot_token)
    try:
        while True:
            raw = await ws.receive_text()
            await state.bridge.handle_bot_message(bot_token, raw)
    except WebSocketDisconnect as exc:
        logger.info("Bot disconnected: {}... from={} code={} reason={}", bot_token[:10], client_addr, exc.code, exc.reason)
    except Exception:
        logger.exception("Bot connection error: {}...", bot_token[:10])
    finally:
        await state.bridge.unregister_bot(bot_token, ws)
