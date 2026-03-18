from fastapi import APIRouter

from infra.log import logger
import services.state as state

router = APIRouter()


@router.post("/api/token")
async def create_token():
    token = await state.token_manager.generate()
    logger.info("Token created via public API: {}...", token[:10])
    return {"code": 0, "token": token}


@router.post("/api/token/validate")
async def validate_token(body: dict):
    token = body.get("token", "")
    valid = await state.token_manager.validate(token)
    logger.debug("Token validate: {}... valid={}", token[:10] if token else "?", valid)
    return {
        "code": 0,
        "valid": valid,
        "bot_connected": await state.bridge.is_bot_connected(token) if valid else False,
    }
