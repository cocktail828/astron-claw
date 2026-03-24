"""Tests for routers/websocket.py — bot WebSocket lifecycle guards."""

from unittest.mock import AsyncMock, patch

import pytest

from infra.errors import Err
from routers.websocket import ws_bot


class _Client:
    host = "127.0.0.1"
    port = 9000


class _FakeWebSocket:
    def __init__(self, headers: dict[str, str] | None = None):
        self.headers = headers or {}
        self.client = _Client()
        self.accept = AsyncMock()
        self.close = AsyncMock()
        self.receive_text = AsyncMock()


class TestWsBot:
    async def test_draining_worker_rejects_new_bot(self):
        ws = _FakeWebSocket({"x-astron-bot-token": "sk-drain"})

        with patch("routers.websocket.state") as mock_state:
            mock_state.token_manager.validate = AsyncMock(return_value=True)
            mock_state.bridge.is_draining = lambda: True
            mock_state.bridge.register_bot = AsyncMock()

            await ws_bot(ws, token="")

            ws.accept.assert_awaited_once()
            ws.close.assert_awaited_once_with(
                code=Err.WS_SERVER_RESTART.status,
                reason=Err.WS_SERVER_RESTART.message,
            )
            mock_state.bridge.register_bot.assert_not_awaited()

    async def test_invalid_token_still_rejected_before_draining_logic(self):
        ws = _FakeWebSocket({"x-astron-bot-token": "sk-invalid"})

        with patch("routers.websocket.state") as mock_state:
            mock_state.token_manager.validate = AsyncMock(return_value=False)
            mock_state.bridge.is_draining = lambda: True

            await ws_bot(ws, token="")

            ws.accept.assert_awaited_once()
            ws.close.assert_awaited_once_with(
                code=Err.WS_INVALID_TOKEN.status,
                reason=Err.WS_INVALID_TOKEN.message,
            )
