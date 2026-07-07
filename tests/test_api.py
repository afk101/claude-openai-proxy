"""OpenAI 兼容接口测试。"""

from fastapi.testclient import TestClient

from src.main import app


def test_chat_completions_endpoint_converts_request_and_response(monkeypatch):
    """接口应以 OpenAI 请求调用 Claude 服务，并返回 OpenAI 响应。"""
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
            "model": "gpt-4o",
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
    payload = response.json()
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"] == {"role": "assistant", "content": "你好"}
    assert payload["usage"] == {
        "prompt_tokens": 4,
        "completion_tokens": 2,
        "total_tokens": 6,
    }
