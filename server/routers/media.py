from fastapi import APIRouter, Form, Header, UploadFile, File

from infra.errors import Err, error_response
from infra.log import logger
from services.media_manager import MAX_FILE_SIZE
import services.state as state

router = APIRouter()


async def _validate_token_header(authorization: str | None) -> str | None:
    """Extract and validate token from Authorization header (Bearer scheme)."""
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        token = parts[1]
    else:
        token = authorization
    if await state.token_manager.validate(token):
        return token
    return None


@router.post("/api/media/upload")
async def upload_media(
    file: UploadFile = File(...),
    sessionId: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
):
    token = await _validate_token_header(authorization)
    if not token:
        return error_response(Err.AUTH_INVALID_TOKEN)

    # Determine file size via seek — avoids reading entire file into memory.
    # FastAPI's UploadFile wraps a SpooledTemporaryFile that spills to disk
    # for files > 1 MB, so large uploads stay on disk rather than in RAM.
    file_obj = file.file
    file_obj.seek(0, 2)
    file_size = file_obj.tell()
    file_obj.seek(0)

    if file_size > MAX_FILE_SIZE:
        logger.warning("Media upload rejected: file too large ({} bytes)", file_size)
        return error_response(Err.MEDIA_FILE_TOO_LARGE)

    mime_type = file.content_type or "application/octet-stream"
    file_name = file.filename or "unnamed"

    result = await state.media_manager.store(file_obj, file_name, file_size, mime_type, sessionId)
    if not result:
        logger.warning("Media upload rejected: invalid file (name={}, mime={})", file_name, mime_type)
        return error_response(Err.MEDIA_INVALID_FILE)

    logger.info("Media uploaded: {} ({} bytes) token={}...", file_name, file_size, token[:10])
    return result
