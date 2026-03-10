"""Tests for services/media_manager.py — ObjectStorage-based MediaManager."""

import io
from unittest.mock import AsyncMock

import pytest

from services.media_manager import MediaManager, MAX_FILE_SIZE


@pytest.fixture()
def mock_storage():
    """Return a mock ObjectStorage."""
    storage = AsyncMock()
    storage.put_object = AsyncMock(return_value="http://localhost:9000/astron-claw-media/sid/photo.png")
    storage.bucket = "astron-claw-media"
    return storage


class TestIsMimeAllowed:
    @pytest.mark.parametrize("mime", [
        "image/png", "image/jpeg", "audio/mp3", "audio/wav",
        "video/mp4", "application/pdf", "text/plain", "text/csv",
        "application/zip", "application/octet-stream",
    ])
    def test_is_mime_allowed_valid(self, mime, mock_storage):
        mm = MediaManager(mock_storage)
        assert mm._is_mime_allowed(mime) is True

    @pytest.mark.parametrize("mime", [
        "application/javascript", "font/woff2", "",
    ])
    def test_is_mime_allowed_invalid(self, mime, mock_storage):
        mm = MediaManager(mock_storage)
        assert mm._is_mime_allowed(mime) is False


class TestStore:
    async def test_store_too_large(self, mock_storage):
        mm = MediaManager(mock_storage)
        result = await mm.store(io.BytesIO(b"x"), "big.bin", MAX_FILE_SIZE + 1, "application/octet-stream")
        assert result is None
        mock_storage.put_object.assert_not_called()

    async def test_store_empty_file(self, mock_storage):
        mm = MediaManager(mock_storage)
        result = await mm.store(io.BytesIO(b""), "empty.txt", 0, "text/plain")
        assert result is None
        mock_storage.put_object.assert_not_called()

    async def test_store_bad_mime(self, mock_storage):
        mm = MediaManager(mock_storage)
        result = await mm.store(io.BytesIO(b"data"), "script.js", 4, "application/javascript")
        assert result is None
        mock_storage.put_object.assert_not_called()

    async def test_store_path_traversal(self, mock_storage):
        mm = MediaManager(mock_storage)
        mock_storage.put_object.return_value = "http://localhost:9000/astron-claw-media/sid/passwd"
        data = b"data"
        result = await mm.store(io.BytesIO(data), "../../etc/passwd", len(data), "text/plain")
        assert result is not None
        assert result["fileName"] == "passwd"
        # S3 key should use sanitized filename
        key = mock_storage.put_object.call_args[0][0]
        assert "/" in key
        assert ".." not in key

    async def test_store_dot_segment_filename(self, mock_storage):
        mm = MediaManager(mock_storage)
        mock_storage.put_object.return_value = "http://localhost:9000/astron-claw-media/sid/unnamed"
        data = b"data"
        result = await mm.store(io.BytesIO(data), "..", len(data), "text/plain")
        assert result is not None
        assert result["fileName"] == "unnamed"

    async def test_store_dotfile_renamed(self, mock_storage):
        mm = MediaManager(mock_storage)
        mock_storage.put_object.return_value = "http://localhost:9000/astron-claw-media/sid/unnamed"
        data = b"data"
        result = await mm.store(io.BytesIO(data), ".hidden", len(data), "text/plain")
        assert result is not None
        assert result["fileName"] == "unnamed"

    async def test_store_success_with_session_id(self, mock_storage):
        mm = MediaManager(mock_storage)
        mock_storage.put_object.return_value = "http://localhost:9000/astron-claw-media/my-session/photo.png"
        data = b"PNG file content"
        result = await mm.store(io.BytesIO(data), "photo.png", len(data), "image/png", "my-session")

        assert result is not None
        assert result["fileName"] == "photo.png"
        assert result["mimeType"] == "image/png"
        assert result["fileSize"] == len(data)
        assert result["sessionId"] == "my-session"
        assert result["downloadUrl"] == "http://localhost:9000/astron-claw-media/my-session/photo.png"

        # Verify S3 put_object was called with correct args
        mock_storage.put_object.assert_called_once()
        args = mock_storage.put_object.call_args[0]
        assert args[0] == "my-session/photo.png"  # key
        assert args[2] == "image/png"              # content_type
        assert args[3] == len(data)                # content_length

    async def test_store_success_without_session_id(self, mock_storage):
        mm = MediaManager(mock_storage)
        mock_storage.put_object.return_value = "http://localhost:9000/astron-claw-media/generated-uuid/photo.png"
        data = b"PNG file content"
        result = await mm.store(io.BytesIO(data), "photo.png", len(data), "image/png")

        assert result is not None
        assert result["sessionId"]  # should be auto-generated UUID
        assert result["downloadUrl"] == "http://localhost:9000/astron-claw-media/generated-uuid/photo.png"


class TestMaxFileSize:
    def test_max_file_size_is_500mb(self):
        assert MAX_FILE_SIZE == 500 * 1024 * 1024
