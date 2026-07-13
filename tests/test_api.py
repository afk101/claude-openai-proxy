"""OpenAI 兼容接口测试。"""

import logging

from fastapi.testclient import TestClient

from src.main import app


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
    assert captured["request"]["max_tokens"] == 200000


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
    assert captured["request"]["system"] == "中文回答"
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
