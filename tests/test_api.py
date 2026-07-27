"""OpenAI 兼容接口测试。"""

import logging

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.main import app


class FakeOpenedStream:
    """提供可控行序列和流中异常的测试流。"""

    def __init__(self, lines=None, exception=None):
        self.lines = lines or []
        self.exception = exception
        self.close_count = 0

    async def iter_lines(self):
        for line in self.lines:
            yield line
        if self.exception:
            raise self.exception

    async def aclose(self):
        self.close_count += 1


def test_chat_completions_endpoint_accepts_arbitrary_model_with_default_token_budget(monkeypatch):
    """接口应接受任意模型，并在未传上限时使用统一默认值。"""
    captured = {}

    async def fake_create_message(claude_request, request_id=None):
        captured["request"] = claude_request
        return {
            "content": [{"type": "text", "text": "你好"}],
            "stop_reason": "end_turn",
            "usage": {},
        }

    import src.api.endpoints as endpoints

    monkeypatch.setattr(endpoints.claude_client, "create_message", fake_create_message)
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"model": "claude-4.8-opus", "messages": [{"role": "user", "content": "你好"}]},
    )

    assert response.status_code == 200
    assert captured["request"]["model"] == "claude-4.8-opus"
    assert captured["request"]["max_tokens"] == 64000


def test_chat_completions_endpoint_converts_request_and_response(monkeypatch, caplog):
    """接口应以 OpenAI 请求调用 Claude 服务，并返回 OpenAI 响应。"""
    caplog.set_level(logging.INFO)
    captured = {}

    async def fake_create_message(claude_request, request_id=None):
        captured["request"] = claude_request
        captured["request_id"] = request_id
        return {
            "id": "msg_api",
            "type": "message",
            "role": "assistant",
            "model": "claude-3-5-sonnet-20241022",
            "content": [{"type": "text", "text": "你好"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 4, "output_tokens": 2},
        }

    import src.api.endpoints as endpoints

    monkeypatch.setattr(endpoints.claude_client, "create_message", fake_create_message)

    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "claude-4.8-opus",
            "messages": [
                {"role": "system", "content": "中文回答"},
                {"role": "user", "content": "你好"},
            ],
            "max_tokens": 64,
        },
    )

    assert response.status_code == 200
    assert captured["request"]["system"] == [
        {
            "type": "text",
            "text": "中文回答",
        }
    ]
    assert captured["request"]["messages"] == [{"role": "user", "content": "你好"}]
    assert captured["request"]["model"] == "claude-4.8-opus"
    payload = response.json()
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"] == {"role": "assistant", "content": "你好"}
    assert payload["usage"] == {
        "prompt_tokens": 4,
        "completion_tokens": 2,
        "total_tokens": 6,
    }
    assert any("chat_completion_received" in record.message for record in caplog.records)
    assert any("chat_completion_upstream_request" in record.message for record in caplog.records)


@pytest.mark.parametrize(
    ("status_code", "detail"),
    [
        (401, "上游 API Key 配额耗尽"),
        (408, "上游请求超时"),
        (503, "上游服务不可用"),
    ],
)
def test_streaming_endpoint_returns_upstream_status_before_response_starts(
    monkeypatch,
    status_code,
    detail,
):
    """预连接 HTTP 错误应保留状态码，而不是先返回 200。"""

    async def fake_create_message_stream(claude_request, request_id=None):
        raise HTTPException(status_code=status_code, detail=detail)

    import src.api.endpoints as endpoints

    monkeypatch.setattr(
        endpoints.claude_client,
        "create_message_stream",
        fake_create_message_stream,
    )
    response = TestClient(app).post(
        "/v1/chat/completions",
        json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}


def test_streaming_endpoint_emits_error_event_after_stream_timeout(
    monkeypatch,
    caplog,
):
    """响应开始后的超时应输出错误 SSE，并可靠关闭上游流。"""
    caplog.set_level(logging.INFO)
    opened_stream = FakeOpenedStream(
        exception=httpx.ReadTimeout("upstream read timed out")
    )

    async def fake_create_message_stream(claude_request, request_id=None):
        return opened_stream

    import src.api.endpoints as endpoints

    monkeypatch.setattr(
        endpoints.claude_client,
        "create_message_stream",
        fake_create_message_stream,
    )
    response = TestClient(app).post(
        "/v1/chat/completions",
        json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert response.text.count('"code": "upstream_timeout"') == 1
    assert response.text.count("data: [DONE]") == 1
    assert '"finish_reason": "stop"' not in response.text
    assert opened_stream.close_count == 1
    assert any(
        "chat_completion_stream_error" in record.message
        and "upstream_timeout" in record.message
        for record in caplog.records
    )
    assert not any(
        "Caught handled exception, but response already started" in record.message
        for record in caplog.records
    )


def test_streaming_endpoint_preserves_successful_stream_and_closes_resources(monkeypatch):
    """正常流式响应应继续转换内容、发送一次结束事件并关闭资源。"""
    opened_stream = FakeOpenedStream(
        lines=[
            'data: {"type":"message_start","message":{"usage":{"input_tokens":2}}}\n',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"你好"}}\n',
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}\n',
            'data: {"type":"message_stop"}\n',
        ]
    )

    async def fake_create_message_stream(claude_request, request_id=None):
        return opened_stream

    import src.api.endpoints as endpoints

    monkeypatch.setattr(
        endpoints.claude_client,
        "create_message_stream",
        fake_create_message_stream,
    )
    response = TestClient(app).post(
        "/v1/chat/completions",
        json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert '"content": "你好"' in response.text
    assert response.text.count("data: [DONE]") == 1
    assert opened_stream.close_count == 1
