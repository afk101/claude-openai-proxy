"""OpenAI 兼容接口测试。"""

import logging
import re

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.main import app
from src.core.zqi_catalog import ZqiRoute


@pytest.fixture(autouse=True)
def stub_zqi_route_resolver(monkeypatch):
    """让既有 endpoint 测试显式隔离目录服务，只验证原有协议转换行为。"""
    import src.api.endpoints as endpoints

    class DefaultRouteResolver:
        async def resolve(self, model):
            return None

    monkeypatch.setattr(endpoints, "zqi_route_resolver", DefaultRouteResolver())


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


def build_successful_upstream_response(stream):
    """构造可供流式与非流式端点消费的成功上游响应。"""
    if stream:
        return httpx.Response(
            200,
            content=(
                'data: {"type":"message_start","message":{"usage":{"input_tokens":1}}}\n\n'
                'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"ok"}}\n\n'
                'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}\n\n'
                'data: {"type":"message_stop"}\n\n'
            ),
        )
    return httpx.Response(
        200,
        json={
            "id": "msg_context",
            "type": "message",
            "role": "assistant",
            "model": "test-model",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    )


def post_and_capture_upstream(monkeypatch, *, stream, headers=None, upstream_api_key=None):
    """从公开端点发起请求并捕获真实上游 HTTP Header。"""
    captured = {}

    async def handler(request):
        captured["headers"] = request.headers
        return build_successful_upstream_response(stream)

    import src.api.endpoints as endpoints

    if upstream_api_key is not None:
        monkeypatch.setattr(endpoints.claude_client, "api_key", upstream_api_key)
    monkeypatch.setattr(endpoints.claude_client, "transport", httpx.MockTransport(handler))
    response = TestClient(app).post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": stream,
        },
    )
    return response, captured["headers"]


@pytest.mark.parametrize(
    ("token_fields", "expected_max_tokens"),
    [
        ({}, None),
        ({"max_tokens": 64}, 64),
        ({"max_completion_tokens": 128}, 128),
    ],
)
def test_chat_completions_endpoint_forwards_only_explicit_token_limit(
    monkeypatch, token_fields, expected_max_tokens
):
    """接口应转发显式输出上限，并在调用方没有提供时省略 max_tokens。"""
    captured = {}

    async def fake_create_message(claude_request, request_id=None, request_context=None, route=None):
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
        json={
            "model": "claude-4.8-opus",
            "messages": [{"role": "user", "content": "你好"}],
            **token_fields,
        },
    )

    assert response.status_code == 200
    assert captured["request"]["model"] == "claude-4.8-opus"
    if expected_max_tokens is None:
        assert "max_tokens" not in captured["request"]
    else:
        assert captured["request"]["max_tokens"] == expected_max_tokens


def test_chat_completions_endpoint_converts_request_and_response(monkeypatch, caplog):
    """接口应以 OpenAI 请求调用 Claude 服务，并返回 OpenAI 响应。"""
    caplog.set_level(logging.INFO)
    captured = {}

    async def fake_create_message(claude_request, request_id=None, request_context=None, route=None):
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


def test_chat_completion_forwards_zqi_route_headers_without_default_key(monkeypatch):
    """命中智企 route 时应按请求级数据替换认证并转发套餐头。"""
    route = ZqiRoute(
        model="pkg/model",
        api_key="package-key",
        headers={
            "X-Ai-Forward-Url": "https://llm.api.zyuncs.com/v1",
            "X-Ai-Forward-Email": "user@example.test",
            "X-Pkg-Model": "1022",
        },
    )

    import src.api.endpoints as endpoints

    headers = endpoints.claude_client.build_headers(route=route)
    assert headers["x-api-key"] == "package-key"
    assert headers["authorization"] == "Bearer package-key"
    assert headers["X-Ai-Forward-Url"] == "https://llm.api.zyuncs.com/v1"
    assert "claude-default" not in headers.values()


def test_unknown_zqi_model_falls_back_to_default_key():
    """目录未命中模型时应继续使用普通上游密钥。"""
    import src.api.endpoints as endpoints

    headers = endpoints.claude_client.build_headers(
        route=ZqiRoute(model="ordinary/model", api_key=None, headers={}),
        request_id="request-id",
    )

    assert headers["x-api-key"] == endpoints.claude_client.api_key
    assert headers["authorization"] == f"Bearer {endpoints.claude_client.api_key}"


@pytest.mark.parametrize("stream", [False, True])
def test_endpoint_preserves_client_context_headers(monkeypatch, stream):
    """上游请求应声明 IDE 来源并保留大小写无关的客户端上下文。"""
    response, captured_headers = post_and_capture_upstream(
        monkeypatch,
        stream=stream,
        headers={
            "x-ClIeNt-TaSk-Id": "client-task-1",
            "X-cLiEnT-tRaCe-Id": "client-trace-1",
            "X-Unrelated-Header": "must-not-forward",
        },
    )
    assert response.status_code == 200
    assert captured_headers["x-src"] == "ide"
    assert captured_headers["x-client-task-id"] == "client-task-1"
    assert captured_headers["x-client-trace-id"] == "client-trace-1"
    assert "x-unrelated-header" not in captured_headers


@pytest.mark.parametrize("stream", [False, True])
def test_endpoint_generates_distinct_missing_context_ids(monkeypatch, stream):
    """客户端未提供上下文时应分别生成格式一致且不同的任务与追踪标识。"""
    response, captured_headers = post_and_capture_upstream(monkeypatch, stream=stream)
    task_id = captured_headers["x-client-task-id"]
    trace_id = captured_headers["x-client-trace-id"]
    assert response.status_code == 200
    assert re.fullmatch(r"[0-9a-f]{32}", task_id)
    assert re.fullmatch(r"[0-9a-f]{32}", trace_id)
    assert task_id != trace_id


@pytest.mark.parametrize(
    ("stream", "headers", "preserved_header", "preserved_value", "generated_header"),
    [
        (stream, headers, preserved_header, preserved_value, generated_header)
        for stream in [False, True]
        for headers, preserved_header, preserved_value, generated_header in [
            (
                {"X-Client-Task-Id": "existing-task", "X-Client-Trace-Id": ""},
                "x-client-task-id",
                "existing-task",
                "x-client-trace-id",
            ),
            (
                {"X-Client-Task-Id": "   ", "X-Client-Trace-Id": "existing-trace"},
                "x-client-trace-id",
                "existing-trace",
                "x-client-task-id",
            ),
        ]
    ],
)
def test_endpoint_only_generates_missing_context_id(
    monkeypatch,
    stream,
    headers,
    preserved_header,
    preserved_value,
    generated_header,
):
    """仅缺失的上下文字段应生成新值，已有字段必须保持不变。"""
    response, captured_headers = post_and_capture_upstream(
        monkeypatch,
        stream=stream,
        headers=headers,
    )
    assert response.status_code == 200
    assert captured_headers[preserved_header] == preserved_value
    assert re.fullmatch(r"[0-9a-f]{32}", captured_headers[generated_header])


@pytest.mark.parametrize("stream", [False, True])
def test_endpoint_keeps_upstream_auth_proxy_controlled(monkeypatch, stream):
    """入站鉴权不得覆盖代理配置的上游鉴权。"""
    response, captured_headers = post_and_capture_upstream(
        monkeypatch,
        stream=stream,
        headers={"Authorization": "Bearer inbound-client-key"},
        upstream_api_key="configured-upstream-key",
    )
    assert response.status_code == 200
    assert captured_headers["authorization"] == "Bearer configured-upstream-key"
    assert captured_headers["x-api-key"] == "configured-upstream-key"


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

    async def fake_create_message_stream(claude_request, request_id=None, request_context=None, route=None):
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

    async def fake_create_message_stream(claude_request, request_id=None, request_context=None, route=None):
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

    async def fake_create_message_stream(claude_request, request_id=None, request_context=None, route=None):
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
