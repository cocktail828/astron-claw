"""Tests for bridge.py pure functions: _translate_bot_event."""

import json

from services.bridge import _translate_bot_event


# ── _translate_bot_event ─────────────────────────────────────────────────────

class TestTranslateBotEventChunk:
    def test_chunk(self):
        params = {"update": {"sessionUpdate": "agent_message_chunk", "content": {"text": "hello"}}}
        result = _translate_bot_event("session/update", params)
        assert result == {"type": "chunk", "content": "hello"}

    def test_final(self):
        params = {"update": {"sessionUpdate": "agent_message_final", "content": {"text": "done"}}}
        result = _translate_bot_event("session/update", params)
        assert result == {"type": "done", "content": "done"}

    def test_thinking(self):
        params = {"update": {"sessionUpdate": "agent_thought_chunk", "content": {"text": "hmm"}}}
        result = _translate_bot_event("session/update", params)
        assert result == {"type": "thinking", "content": "hmm"}


class TestTranslateBotEventToolCall:
    def test_tool_call(self):
        params = {"update": {"sessionUpdate": "tool_call", "title": "web_search", "content": "query foo"}}
        result = _translate_bot_event("session/update", params)
        assert result == {"type": "tool_call", "name": "web_search", "input": "query foo"}

    def test_tool_call_non_str_content(self):
        obj = {"query": "foo", "limit": 10}
        params = {"update": {"sessionUpdate": "tool_call", "title": "search", "content": obj}}
        result = _translate_bot_event("session/update", params)
        assert result["type"] == "tool_call"
        assert result["input"] == json.dumps(obj)


class TestTranslateBotEventToolResult:
    def test_tool_result_str(self):
        params = {"update": {
            "sessionUpdate": "tool_result",
            "title": "web_search",
            "status": "completed",
            "content": "Found 3 results",
        }}
        result = _translate_bot_event("session/update", params)
        assert result == {"type": "tool_result", "name": "web_search", "status": "completed", "content": "Found 3 results"}

    def test_tool_result_dict(self):
        params = {"update": {
            "sessionUpdate": "tool_result",
            "title": "search",
            "status": "completed",
            "content": {"text": "extracted text", "extra": "stuff"},
        }}
        result = _translate_bot_event("session/update", params)
        assert result["content"] == "extracted text"

    def test_tool_result_other(self):
        data = [1, 2, 3]
        params = {"update": {
            "sessionUpdate": "tool_result",
            "title": "calc",
            "status": "completed",
            "content": data,
        }}
        result = _translate_bot_event("session/update", params)
        assert result["content"] == json.dumps(data)


class TestTranslateBotEventMedia:
    def test_agent_media(self):
        params = {"update": {
            "sessionUpdate": "agent_media",
            "content": {
                "text": "Here is the file",
                "msgType": "image",
                "media": {
                    "downloadUrl": "http://localhost:9000/astron-claw-media/sid/photo.png",
                    "fileName": "photo.png",
                    "mimeType": "image/png",
                    "fileSize": 1024,
                },
            },
        }}
        result = _translate_bot_event("session/update", params)
        assert result["type"] == "media"
        assert result["data"]["type"] == "url"
        assert result["data"]["content"] == "http://localhost:9000/astron-claw-media/sid/photo.png"
        assert result["data"]["caption"] == "Here is the file"

    def test_agent_media_no_caption(self):
        params = {"update": {
            "sessionUpdate": "agent_media",
            "content": {
                "media": {
                    "downloadUrl": "http://localhost:9000/astron-claw-media/sid/photo.png",
                },
            },
        }}
        result = _translate_bot_event("session/update", params)
        assert result["type"] == "media"
        assert result["data"]["type"] == "url"
        assert "caption" not in result["data"]

    def test_agent_media_missing_download_url(self):
        """agent_media with empty downloadUrl should return None."""
        params = {"update": {
            "sessionUpdate": "agent_media",
            "content": {
                "text": "Here is the file",
                "media": {},
            },
        }}
        result = _translate_bot_event("session/update", params)
        assert result is None

    def test_agent_media_no_media_field(self):
        """agent_media without media dict should return None."""
        params = {"update": {
            "sessionUpdate": "agent_media",
            "content": {
                "text": "oops",
            },
        }}
        result = _translate_bot_event("session/update", params)
        assert result is None


class TestTranslateBotEventFallback:
    def test_unknown_update_with_text(self):
        params = {"update": {"sessionUpdate": "something_new", "content": {"text": "fallback data"}}}
        result = _translate_bot_event("session/update", params)
        assert result == {"type": "chunk", "content": "fallback data"}

    def test_unknown_update_no_text(self):
        params = {"update": {"sessionUpdate": "something_new", "content": {"data": 123}}}
        result = _translate_bot_event("session/update", params)
        assert result is None

    def test_unknown_method(self):
        result = _translate_bot_event("other/method", {"foo": "bar"})
        assert result is None
