"""Responses 公开入口的端到端行为测试。"""

import asyncio
import gzip
import json
import logging
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from src.core.config import Config
from src.core.zqi_catalog import ZqiCatalogClient, ZqiRouteResolver
from src.main import app


class _StaticAsyncByteStream(httpx.AsyncByteStream):
    """模拟真实 transport 返回的尚未消费异步响应流。"""

    def __init__(self, chunks):
        self.chunks = chunks
        self.close_count = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.close_count += 1


class _TrackingMockTransport(httpx.MockTransport):
    """记录上游 HTTP client 是否最终关闭其 transport。"""

    def __init__(self, handler):
        super().__init__(handler)
        self.close_count = 0

    async def aclose(self):
        self.close_count += 1
        await super().aclose()


class _FailingAsyncByteStream(httpx.AsyncByteStream):
    """先产生不完整字节再抛错，用于模拟上游 body 读取阶段中断。"""

    def __init__(self, exception, first_chunk=b"partial-body-must-not-escape"):
        self.exception = exception
        self.first_chunk = first_chunk
        self.close_count = 0

    async def __aiter__(self):
        yield self.first_chunk
        raise self.exception

    async def aclose(self):
        self.close_count += 1


class _FailingCloseTransport(_TrackingMockTransport):
    """模拟 HTTP client 在最终关闭 transport 时报告异常。"""

    async def aclose(self):
        self.close_count += 1
        raise RuntimeError("simulated transport close failure")


class _GatedAsyncByteStream(httpx.AsyncByteStream):
    """让 raw body 读取停在可观察 gate，用于验证取消不依赖上游继续产出。"""

    def __init__(self, first_chunk=None):
        self.first_chunk = first_chunk
        self.read_started = asyncio.Event()
        self.read_cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.close_count = 0

    async def __aiter__(self):
        if self.first_chunk is not None:
            yield self.first_chunk
        self.read_started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.read_cancelled.set()
            raise
        yield b"unexpected-after-release"

    async def aclose(self):
        self.close_count += 1


async def _call_app_directly(raw_body: bytes, headers=None):
    """直接驱动公开 ASGI 接口，以观察未被测试客户端改写的响应字节和 Header。"""
    received_request = False
    sent_messages = []
    raw_headers = [(b"host", b"local.test"), (b"content-type", b"application/json")]
    raw_headers.extend(headers or [])

    async def receive():
        nonlocal received_request
        if not received_request:
            received_request = True
            return {"type": "http.request", "body": raw_body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        sent_messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/responses",
        "raw_path": b"/v1/responses",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("local.test", 80),
        "root_path": "",
        "state": {},
    }
    await app(scope, receive, send)
    start = next(message for message in sent_messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in sent_messages
        if message["type"] == "http.response.body"
    )
    return start["status"], start["headers"], body


async def _call_stream_app_directly(raw_body: bytes, headers=None):
    """以 ASGI 2.4 驱动有限流，避免客户端库自动解压或折叠重复 Header。"""
    received_request = False
    sent_messages = []
    raw_headers = [(b"host", b"local.test"), (b"content-type", b"application/json")]
    raw_headers.extend(headers or [])

    async def receive():
        nonlocal received_request
        if not received_request:
            received_request = True
            return {"type": "http.request", "body": raw_body, "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        sent_messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/responses",
        "raw_path": b"/v1/responses",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("local.test", 80),
        "root_path": "",
        "state": {},
    }
    await app(scope, receive, send)
    start = next(message for message in sent_messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in sent_messages
        if message["type"] == "http.response.body"
    )
    return start["status"], start["headers"], body


def _write_fake_wiscode_auth(tmp_path: Path) -> Path:
    """写入只供 MockTransport 使用的虚假认证，避免测试读取真实用户配置。"""
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "host": "catalog.example.test",
                "access_token": "fake-catalog-token",
                "mail": "tester@example.test",
            }
        ),
        encoding="utf-8",
    )
    return auth_path


def _install_empty_catalog(monkeypatch, tmp_path: Path) -> None:
    """让公开入口经过真实 resolver，并由虚假目录确认模型完全未命中。"""
    _install_catalog(monkeypatch, tmp_path, [])


def _install_catalog(monkeypatch, tmp_path: Path, packages) -> None:
    """让公开入口使用指定的合法目录，测试仍从 HTTP seam 观察最终路由。"""
    import src.api.endpoints as endpoints

    async def catalog_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"context": {"code": 0}, "data": {"list": packages}},
        )

    catalog = ZqiCatalogClient(
        auth_path=_write_fake_wiscode_auth(tmp_path),
        transport=httpx.MockTransport(catalog_handler),
    )
    monkeypatch.setattr(endpoints, "zqi_route_resolver", ZqiRouteResolver(catalog))


def _responses_package(model_name="pkg/model", **overrides):
    """构造仅含 Responses 能力的最小合法套餐目录项。"""
    package = {
        "id": 2048,
        "identifier": "zyzj_package",
        "expireAt": "2999-01-01T00:00:00+00:00",
        "exhausted": False,
        "apiKey": {"full": "fake-package-key"},
        "models": [
            {"name": model_name, "apiNames": ["responses"], "enabled": True}
        ],
    }
    package.update(overrides)
    return package


def _install_counted_empty_catalog(monkeypatch, tmp_path: Path):
    """安装记录调用次数的空目录，用于证明本地错误不会触发外部目录请求。"""
    import src.api.endpoints as endpoints

    requests = []

    async def catalog_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"context": {"code": 0}, "data": {"list": []}},
        )

    catalog = ZqiCatalogClient(
        auth_path=_write_fake_wiscode_auth(tmp_path),
        transport=httpx.MockTransport(catalog_handler),
    )
    monkeypatch.setattr(endpoints, "zqi_route_resolver", ZqiRouteResolver(catalog))
    return requests


def test_non_stream_responses_forwards_original_body_with_ordinary_key(
    monkeypatch, tmp_path
):
    """目录未命中时，公开入口应以普通密钥原样转发请求和响应。"""
    import src.api.endpoints as endpoints

    captured = {}
    raw_body = (
        b'{  "model" : "ordinary/model", "input" : "\xe4\xbd\xa0\xe5\xa5\xbd",'
        b' "unknown_future_field": {"kept":true}, "stream": false }'
    )
    upstream_body = b'{"id":"resp_1","status":"completed","unknown":true}'

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["body"] = await request.aread()
        return httpx.Response(
            200,
            stream=_StaticAsyncByteStream([upstream_body]),
            headers={"Content-Type": "application/json", "X-Upstream-Version": "future"},
        )

    _install_empty_catalog(monkeypatch, tmp_path)
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test/aiproxy")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(
        endpoints.responses_client,
        "transport",
        httpx.MockTransport(upstream_handler),
    )

    response = TestClient(app).post(
        "/v1/responses",
        content=raw_body,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert response.content == upstream_body
    assert response.headers["x-upstream-version"] == "future"
    assert captured["url"] == "https://proxy.example.test/aiproxy/v1/responses"
    assert captured["body"] == raw_body
    assert captured["headers"]["authorization"] == "Bearer fake-ordinary-key"
    assert captured["headers"]["x-api-key"] == "fake-ordinary-key"


def test_legacy_chat_completions_route_is_not_public():
    """最终产品面只提供 Responses Create，旧 Chat 路径必须彻底退出。"""
    response = TestClient(app).post("/v1/chat/completions", json={})

    assert response.status_code == 404


def test_responses_endpoint_uses_package_key_and_all_package_headers(
    monkeypatch, tmp_path
):
    """Responses 目录命中时，公开入口应使用套餐身份和完整套餐 Header。"""
    import src.api.endpoints as endpoints

    captured = {}

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(
            200,
            stream=_StaticAsyncByteStream([b'{"status":"completed"}']),
        )

    _install_catalog(monkeypatch, tmp_path, [_responses_package()])
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(
        endpoints.responses_client,
        "transport",
        httpx.MockTransport(upstream_handler),
    )

    response = TestClient(app).post(
        "/v1/responses",
        content=b'{"model":"pkg/model","input":"hello"}',
    )

    headers = captured.get("headers")
    assert response.status_code == 200
    assert headers["authorization"] == "Bearer fake-package-key"
    assert headers["x-api-key"] == "fake-package-key"
    assert headers["x-ai-forward-url"] == "https://llm.api.zyuncs.com/v1"
    assert headers["x-pkg-model"] == "2048"
    assert headers["x-ai-forward-email"] == "tester@example.test"


@pytest.mark.parametrize("api_names", [["messages"], ["Responses"], []])
def test_responses_http_parameters_cannot_select_another_catalog_protocol(
    monkeypatch, tmp_path, api_names
):
    """query 和未知 body 字段都不能把 Responses 入口切换为其他协议。"""
    import src.api.endpoints as endpoints

    model_requests = []
    catalog_package = _responses_package()
    catalog_package["models"][0]["apiNames"] = api_names

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        model_requests.append(request)
        return httpx.Response(
            200,
            stream=_StaticAsyncByteStream([b"unexpected"]),
        )

    _install_catalog(monkeypatch, tmp_path, [catalog_package])
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(
        endpoints.responses_client,
        "transport",
        httpx.MockTransport(upstream_handler),
    )

    response = TestClient(app).post(
        "/v1/responses?api_name=messages&protocol=messages",
        content=(
            b'{"model":"pkg/model","input":"hello",'
            b'"api_name":"messages","protocol":"messages"}'
        ),
    )

    assert response.status_code == 400
    assert "responses" in response.json()["detail"]
    assert model_requests == []


def test_unavailable_responses_packages_return_503_without_ordinary_fallback(
    monkeypatch, tmp_path
):
    """目录已出现模型但套餐不可用时，普通密钥不能绕过智企限制。"""
    import src.api.endpoints as endpoints

    model_requests = []
    unavailable_package = _responses_package(
        expireAt="2000-01-01T00:00:00+00:00"
    )

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        model_requests.append(request)
        return httpx.Response(
            200,
            stream=_StaticAsyncByteStream([b"unexpected"]),
        )

    _install_catalog(monkeypatch, tmp_path, [unavailable_package])
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(
        endpoints.responses_client,
        "transport",
        httpx.MockTransport(upstream_handler),
    )

    response = TestClient(app).post(
        "/v1/responses",
        content=b'{"model":"pkg/model","input":"hello"}',
    )

    assert response.status_code == 503
    assert "套餐已过期" in response.json()["detail"]
    assert model_requests == []


@pytest.mark.parametrize(
    ("failure_kind", "expected_status"),
    [
        ("missing_auth", 500),
        ("invalid_token", 401),
        ("catalog_http", 502),
        ("catalog_json", 502),
        ("catalog_schema", 502),
    ],
)
def test_catalog_and_auth_failures_never_use_ordinary_key(
    monkeypatch, tmp_path, failure_kind, expected_status
):
    """无法可靠判断目录命中时必须 fail closed，不能静默走普通密钥。"""
    import src.api.endpoints as endpoints

    model_requests = []
    auth_path = tmp_path / "auth.json"
    if failure_kind == "invalid_token":
        auth_path.write_text(
            json.dumps(
                {
                    "host": "catalog.example.test",
                    "access_token": "",
                }
            ),
            encoding="utf-8",
        )
    elif failure_kind != "missing_auth":
        auth_path = _write_fake_wiscode_auth(tmp_path)

    async def catalog_handler(request: httpx.Request) -> httpx.Response:
        if failure_kind == "catalog_http":
            return httpx.Response(503, content=b"catalog unavailable")
        if failure_kind == "catalog_json":
            return httpx.Response(200, content=b"not-json")
        if failure_kind == "catalog_schema":
            return httpx.Response(
                200,
                json={"context": {"code": 0}, "data": {"list": {}}},
            )
        return httpx.Response(
            200,
            json={"context": {"code": 0}, "data": {"list": []}},
        )

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        model_requests.append(request)
        return httpx.Response(
            200,
            stream=_StaticAsyncByteStream([b"unexpected"]),
        )

    catalog = ZqiCatalogClient(
        auth_path=auth_path,
        transport=httpx.MockTransport(catalog_handler),
    )
    monkeypatch.setattr(endpoints, "zqi_route_resolver", ZqiRouteResolver(catalog))
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(
        endpoints.responses_client,
        "transport",
        httpx.MockTransport(upstream_handler),
    )

    response = TestClient(app).post(
        "/v1/responses",
        content=b'{"model":"ordinary/model","input":"hello"}',
    )

    assert response.status_code == expected_status
    assert model_requests == []


def test_ordinary_key_configuration_prefers_claude_key(monkeypatch):
    """普通回退密钥保持 CLAUDE_API_KEY 优先、ANTHROPIC_API_KEY 兼容。"""
    monkeypatch.setenv("CLAUDE_API_KEY", "fake-claude-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic-key")
    assert Config().claude_api_key == "fake-claude-key"

    monkeypatch.delenv("CLAUDE_API_KEY")
    assert Config().claude_api_key == "fake-anthropic-key"


def test_invalid_routing_envelope_returns_400_before_external_requests(
    monkeypatch, tmp_path
):
    """影响路由的非法或重复字段应在任何外部请求前被拒绝。"""
    import src.api.endpoints as endpoints

    catalog_requests = _install_counted_empty_catalog(monkeypatch, tmp_path)
    upstream_requests = []

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        return httpx.Response(200, stream=_StaticAsyncByteStream([b"unexpected"]))

    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(
        endpoints.responses_client,
        "transport",
        httpx.MockTransport(upstream_handler),
    )
    invalid_bodies = [
        b"not-json",
        b"[]",
        b'{"input":"hello"}',
        b'{"model":42,"input":"hello"}',
        b'{"model":"   ","input":"hello"}',
        b'{"model":"ordinary/model","stream":"false"}',
        b'{"model":"first","model":"second"}',
        b'{"model":"ordinary/model","stream":false,"stream":true}',
    ]

    client = TestClient(app)
    for raw_body in invalid_bodies:
        response = client.post(
            "/v1/responses",
            content=raw_body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    assert catalog_requests == []
    assert upstream_requests == []


@pytest.mark.parametrize(
    "invalid_base_url",
    [
        None,
        "",
        "proxy.example.test/aiproxy",
        "ftp://proxy.example.test/aiproxy",
        "https://proxy.example.test/aiproxy?credential=hidden",
        "https://proxy.example.test/aiproxy#fragment",
        "https://proxy.example.test/v1",
        "https://proxy.example.test/aiproxy/v1/",
        "https://proxy.example.test/aiproxy/v1/responses/",
        "https://proxy.example.test:not-a-port/aiproxy",
        "https://[invalid-ipv6/aiproxy",
        "https://bad host.example.test/aiproxy",
        "https://proxy.example.test/aiproxy?",
        "https://proxy.example.test/aiproxy#",
    ],
)
def test_invalid_base_url_returns_redacted_500_before_catalog(
    monkeypatch, tmp_path, invalid_base_url
):
    """Responses 服务根地址缺失或非法时应快速失败，且不得回显配置值。"""
    import src.api.endpoints as endpoints

    catalog_requests = _install_counted_empty_catalog(monkeypatch, tmp_path)
    upstream_requests = []

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        return httpx.Response(200, stream=_StaticAsyncByteStream([b"unexpected"]))

    monkeypatch.setattr(endpoints.responses_client, "base_url", invalid_base_url)
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(
        endpoints.responses_client,
        "transport",
        httpx.MockTransport(upstream_handler),
    )

    response = TestClient(app).post(
        "/v1/responses",
        content=b'{"model":"ordinary/model","input":"hello"}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "CLAUDE_BASE_URL 配置缺失或格式非法"}
    if invalid_base_url:
        assert invalid_base_url not in response.text
    assert catalog_requests == []
    assert upstream_requests == []


def test_missing_ordinary_key_returns_500_without_model_upstream(
    monkeypatch, tmp_path
):
    """目录未命中且普通密钥缺失时，应在模型上游连接前返回配置错误。"""
    import src.api.endpoints as endpoints

    catalog_requests = _install_counted_empty_catalog(monkeypatch, tmp_path)
    upstream_requests = []

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        return httpx.Response(200, stream=_StaticAsyncByteStream([b"unexpected"]))

    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", None)
    monkeypatch.setattr(
        endpoints.responses_client,
        "transport",
        httpx.MockTransport(upstream_handler),
    )

    response = TestClient(app).post(
        "/v1/responses",
        content=b'{"model":"ordinary/model","input":"hello"}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": "普通上游密钥未配置，请设置 CLAUDE_API_KEY 或 ANTHROPIC_API_KEY"
    }
    assert len(catalog_requests) == 1
    assert upstream_requests == []
    assert endpoints.responses_client.active_requests == set()


def test_invalid_proxy_key_returns_401_before_catalog_or_model_upstream(
    monkeypatch, tmp_path
):
    """代理访问密钥错误时，鉴权必须成为最外层门禁。"""
    import src.api.endpoints as endpoints

    catalog_requests = _install_counted_empty_catalog(monkeypatch, tmp_path)
    upstream_requests = []

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        upstream_requests.append(request)
        return httpx.Response(200, stream=_StaticAsyncByteStream([b"unexpected"]))

    monkeypatch.setattr(endpoints.config, "client_api_key", "fake-proxy-access-key")
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(
        endpoints.responses_client,
        "transport",
        httpx.MockTransport(upstream_handler),
    )

    response = TestClient(app).post(
        "/v1/responses",
        content=b'{"model":"ordinary/model","input":"hello"}',
        headers={"Authorization": "Bearer wrong-client-key"},
    )

    assert response.status_code == 401
    assert catalog_requests == []
    assert upstream_requests == []


@pytest.mark.parametrize(
    "client_headers",
    [
        {"Authorization": "Bearer fake-proxy-access-key"},
        {"X-Api-Key": "fake-proxy-access-key"},
    ],
)
def test_valid_proxy_key_allows_request_but_upstream_uses_ordinary_key(
    monkeypatch, tmp_path, client_headers
):
    """两种代理凭据均可放行，但上游身份只能来自普通密钥配置。"""
    import src.api.endpoints as endpoints

    _install_empty_catalog(monkeypatch, tmp_path)
    captured = {}

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(
            200,
            stream=_StaticAsyncByteStream([b'{"status":"completed"}']),
        )

    monkeypatch.setattr(endpoints.config, "client_api_key", "fake-proxy-access-key")
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(
        endpoints.responses_client,
        "transport",
        httpx.MockTransport(upstream_handler),
    )

    response = TestClient(app).post(
        "/v1/responses",
        content=b'{"model":"ordinary/model","input":"hello"}',
        headers=client_headers,
    )

    assert response.status_code == 200
    assert captured["headers"]["authorization"] == "Bearer fake-ordinary-key"
    assert captured["headers"]["x-api-key"] == "fake-ordinary-key"


def test_upstream_headers_are_controlled_and_preserve_client_context(
    monkeypatch, tmp_path
):
    """只允许代理选定字段进入上游，并保留调用方提供的 task/trace 标识。"""
    import src.api.endpoints as endpoints

    _install_empty_catalog(monkeypatch, tmp_path)
    captured = {}
    raw_body = b'{"model":"ordinary/model","input":"hello"}'

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(
            200,
            stream=_StaticAsyncByteStream([b'{"status":"completed"}']),
        )

    monkeypatch.setattr(endpoints.config, "client_api_key", None)
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(
        endpoints.responses_client,
        "transport",
        httpx.MockTransport(upstream_handler),
    )

    response = TestClient(app).post(
        "/v1/responses",
        content=raw_body,
        headers=[
            ("Authorization", "Bearer inbound-credential"),
            ("X-Api-Key", "inbound-api-key"),
            ("Cookie", "session=must-not-forward"),
            ("Host", "attacker.example.test"),
            ("Content-Length", "999"),
            ("X-Unrelated", "must-not-forward"),
            ("X-Client-Task-Id", "client-task-original"),
            ("X-Client-Trace-Id", "client-trace-original"),
            ("Accept-Encoding", "gzip"),
            ("Accept-Encoding", "br"),
        ],
    )

    headers = captured["headers"]
    assert response.status_code == 200
    assert headers["content-type"] == "application/json"
    assert headers["authorization"] == "Bearer fake-ordinary-key"
    assert headers["x-api-key"] == "fake-ordinary-key"
    assert headers["x-src"] == "ide"
    assert headers["x-client-task-id"] == "client-task-original"
    assert headers["x-client-trace-id"] == "client-trace-original"
    assert headers["accept-encoding"] == "gzip, br"
    assert headers["host"] == "proxy.example.test"
    assert headers["content-length"] == str(len(raw_body))
    assert "x-request-id" in headers
    assert "cookie" not in headers
    assert "x-unrelated" not in headers
    assert "anthropic-version" not in headers
    assert "x-ai-forward-url" not in headers
    assert "x-ai-forward-email" not in headers
    assert "x-pkg-model" not in headers


def test_missing_context_ids_are_distinct_and_empty_accept_encoding_uses_identity(
    monkeypatch, tmp_path
):
    """空上下文分别生成新 ID，调用方未声明压缩能力时只请求 identity。"""
    import src.api.endpoints as endpoints

    _install_empty_catalog(monkeypatch, tmp_path)
    captured = {}

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(
            200,
            stream=_StaticAsyncByteStream([b'{"status":"completed"}']),
        )

    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(
        endpoints.responses_client,
        "transport",
        httpx.MockTransport(upstream_handler),
    )

    response = TestClient(app).post(
        "/v1/responses",
        content=b'{"model":"ordinary/model","input":"hello"}',
        headers={
            "X-Client-Task-Id": "   ",
            "X-Client-Trace-Id": "",
            "Accept-Encoding": "",
        },
    )

    headers = captured["headers"]
    assert response.status_code == 200
    assert re.fullmatch(r"[0-9a-f]{32}", headers["x-client-task-id"])
    assert re.fullmatch(r"[0-9a-f]{32}", headers["x-client-trace-id"])
    assert headers["x-client-task-id"] != headers["x-client-trace-id"]
    assert headers["accept-encoding"] == "identity"


@pytest.mark.parametrize(
    ("status_code", "upstream_body", "content_type"),
    [
        (200, b'{"status":"completed"}', "application/json"),
        (402, b'{"error":{"message":"quota exhausted"}}', "application/json"),
        (429, b"rate limited as plain text", "text/plain; charset=utf-8"),
        (500, b"\x00opaque-upstream-error\xff", "application/octet-stream"),
    ],
)
def test_complete_upstream_responses_preserve_status_body_and_end_to_end_headers(
    monkeypatch, tmp_path, status_code, upstream_body, content_type
):
    """完整取得的成功或错误响应都应保持原始状态、body 和端到端 Header。"""
    import src.api.endpoints as endpoints

    _install_empty_catalog(monkeypatch, tmp_path)

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            stream=_StaticAsyncByteStream([upstream_body]),
            headers={
                "Content-Type": content_type,
                "Retry-After": "17",
                "X-Future-Response-Header": "preserved",
            },
        )

    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(
        endpoints.responses_client,
        "transport",
        httpx.MockTransport(upstream_handler),
    )

    response = TestClient(app).post(
        "/v1/responses",
        content=b'{"model":"ordinary/model","input":"hello"}',
        headers={"Accept-Encoding": "identity"},
    )

    assert response.status_code == status_code
    assert response.content == upstream_body
    assert response.headers["content-type"] == content_type
    assert response.headers["retry-after"] == "17"
    assert response.headers["x-future-response-header"] == "preserved"


def test_direct_asgi_preserves_gzip_and_duplicate_headers_while_filtering_hops(
    monkeypatch, tmp_path
):
    """原始 ASGI 响应应保持压缩配对及重复字段，并剔除当前连接专属 Header。"""
    import src.api.endpoints as endpoints

    _install_empty_catalog(monkeypatch, tmp_path)
    compressed_body = gzip.compress(b'{"status":"completed"}')
    response_stream = _StaticAsyncByteStream(
        [compressed_body[:5], compressed_body[5:]]
    )
    captured = {}

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        captured["accept_encoding"] = request.headers["accept-encoding"]
        return httpx.Response(
            206,
            stream=response_stream,
            headers=[
                (b"Content-Type", b"application/json"),
                (b"Content-Encoding", b"gzip"),
                (b"Set-Cookie", b"first=1"),
                (b"Set-Cookie", b"second=2"),
                (b"X-Future", b"preserved"),
                (b"Connection", b"X-Dynamic-Hop, Keep-Alive"),
                (b"X-Dynamic-Hop", b"remove-me"),
                (b"Keep-Alive", b"timeout=5"),
                (b"Transfer-Encoding", b"chunked"),
                (b"Content-Length", b"999"),
                (b"Server", b"hidden-server"),
                (b"Date", b"Thu, 01 Jan 1970 00:00:00 GMT"),
            ],
        )

    transport = _TrackingMockTransport(upstream_handler)
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(endpoints.responses_client, "transport", transport)

    status_code, raw_headers, response_body = asyncio.run(
        _call_app_directly(b'{"model":"ordinary/model","input":"hello"}')
    )

    normalized_headers = [(name.lower(), value) for name, value in raw_headers]
    header_names = {name for name, _ in normalized_headers}
    assert status_code == 206
    assert response_body == compressed_body
    assert (b"content-encoding", b"gzip") in normalized_headers
    assert [
        value for name, value in normalized_headers if name == b"set-cookie"
    ] == [b"first=1", b"second=2"]
    assert (b"x-future", b"preserved") in normalized_headers
    assert b"connection" not in header_names
    assert b"x-dynamic-hop" not in header_names
    assert b"keep-alive" not in header_names
    assert b"transfer-encoding" not in header_names
    assert b"content-length" not in header_names
    assert b"server" not in header_names
    assert b"date" not in header_names
    assert captured["accept_encoding"] == "identity"
    assert response_stream.close_count == 1
    assert transport.close_count == 1
    assert endpoints.responses_client.active_requests == set()


@pytest.mark.parametrize(
    ("upstream_exception", "expected_status", "expected_detail"),
    [
        (
            httpx.ReadError("upstream body interrupted"),
            502,
            "连接上游 Responses 服务失败，请稍后重试",
        ),
        (
            httpx.ReadTimeout("upstream body timed out"),
            504,
            "连接上游 Responses 服务超时，请稍后重试",
        ),
    ],
)
def test_incomplete_upstream_body_maps_error_and_closes_resources_once(
    monkeypatch,
    tmp_path,
    upstream_exception,
    expected_status,
    expected_detail,
):
    """下游开始前的读取中断不得泄露部分 body，所有上游资源只能关闭一次。"""
    import src.api.endpoints as endpoints

    _install_empty_catalog(monkeypatch, tmp_path)
    response_stream = _FailingAsyncByteStream(upstream_exception)

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=response_stream)

    transport = _TrackingMockTransport(upstream_handler)
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(endpoints.responses_client, "transport", transport)

    response = TestClient(app).post(
        "/v1/responses",
        content=b'{"model":"ordinary/model","input":"hello"}',
        headers={"Accept-Encoding": "identity"},
    )

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert b"partial-body-must-not-escape" not in response.content
    assert response_stream.close_count == 1
    assert transport.close_count == 1
    assert endpoints.responses_client.active_requests == set()


def test_upstream_connect_failure_returns_502_and_clears_active_request(
    monkeypatch, tmp_path
):
    """尚未取得响应的连接失败也应成为脱敏 502，并清理客户端与活动记录。"""
    import src.api.endpoints as endpoints

    _install_empty_catalog(monkeypatch, tmp_path)

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private network detail", request=request)

    transport = _TrackingMockTransport(upstream_handler)
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(endpoints.responses_client, "transport", transport)

    response = TestClient(app).post(
        "/v1/responses",
        content=b'{"model":"ordinary/model","input":"hello"}',
        headers={"Accept-Encoding": "identity"},
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": "连接上游 Responses 服务失败，请稍后重试"
    }
    assert "private network detail" not in response.text
    assert transport.close_count == 1
    assert endpoints.responses_client.active_requests == set()


def test_responses_logs_exclude_bodies_and_credentials(
    monkeypatch, tmp_path, caplog
):
    """运行日志只记录元数据，不得包含请求、响应或任何访问凭据。"""
    import src.api.endpoints as endpoints

    _install_empty_catalog(monkeypatch, tmp_path)
    request_secret = "request-body-private-marker"
    response_secret = b"response-body-private-marker"

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402,
            stream=_StaticAsyncByteStream([response_secret]),
            headers={"Content-Type": "text/plain"},
        )

    monkeypatch.setattr(endpoints.config, "client_api_key", "fake-client-access-marker")
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-upstream-key-marker")
    monkeypatch.setattr(
        endpoints.responses_client,
        "transport",
        httpx.MockTransport(upstream_handler),
    )
    caplog.set_level(logging.INFO)

    response = TestClient(app).post(
        "/v1/responses",
        content=(
            '{"model":"ordinary/model","input":"%s"}' % request_secret
        ).encode(),
        headers={"Authorization": "Bearer fake-client-access-marker"},
    )

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert response.status_code == 402
    assert "responses_received" in log_text
    assert "responses_completed" in log_text
    assert request_secret not in log_text
    assert response_secret.decode() not in log_text
    assert "fake-client-access-marker" not in log_text
    assert "fake-upstream-key-marker" not in log_text
    assert "fake-catalog-token" not in log_text


def test_transport_close_failure_does_not_replace_complete_upstream_response(
    monkeypatch, tmp_path
):
    """响应已完整读取后，清理异常不得覆盖响应，活动记录仍必须移除。"""
    import src.api.endpoints as endpoints

    _install_empty_catalog(monkeypatch, tmp_path)
    response_stream = _StaticAsyncByteStream([b'{"status":"completed"}'])

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=response_stream)

    transport = _FailingCloseTransport(upstream_handler)
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(endpoints.responses_client, "transport", transport)

    response = TestClient(app).post(
        "/v1/responses",
        content=b'{"model":"ordinary/model","input":"hello"}',
        headers={"Accept-Encoding": "identity"},
    )

    assert response.status_code == 200
    assert response.content == b'{"status":"completed"}'
    assert response_stream.close_count == 1
    assert transport.close_count == 1
    assert endpoints.responses_client.active_requests == set()


def test_stream_responses_preserves_native_sse_without_synthetic_events(
    monkeypatch, tmp_path
):
    """公开流式入口应逐字节保留未知事件、注释、空行和上游终态。"""
    import src.api.endpoints as endpoints

    _install_empty_catalog(monkeypatch, tmp_path)
    upstream_body = (
        b": upstream-comment\n\n"
        b"event: response.future_event\n"
        b"data: {\"part\":1}\n"
        b"data: {\"part\":2}\n\n"
        b"event: response.completed\n"
        b"data: {\"type\":\"response.completed\"}\n\n"
    )
    response_stream = _StaticAsyncByteStream(
        [upstream_body[:17], upstream_body[17:61], upstream_body[61:]]
    )

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=response_stream,
            headers={"Content-Type": "text/event-stream"},
        )

    transport = _TrackingMockTransport(upstream_handler)
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(endpoints.responses_client, "transport", transport)

    response = TestClient(app).post(
        "/v1/responses",
        content=b'{"model":"ordinary/model","input":"hello","stream":true}',
        headers={"Accept-Encoding": "identity"},
    )

    assert response.status_code == 200
    assert response.content == upstream_body
    assert response.headers["content-type"] == "text/event-stream"
    assert b"[DONE]" not in response.content
    assert response_stream.close_count == 1
    assert transport.close_count == 1
    assert endpoints.responses_client.active_requests == set()


def test_package_stream_uses_package_identity_and_preserves_context(
    monkeypatch, tmp_path
):
    """套餐流式请求应复用原 body、套餐身份和调用方提供的上下文 Header。"""
    import src.api.endpoints as endpoints

    _install_catalog(monkeypatch, tmp_path, [_responses_package()])
    captured = {}
    raw_body = b'{ "model":"pkg/model", "input":"hello", "stream":true }'

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = await request.aread()
        captured["headers"] = request.headers
        return httpx.Response(
            200,
            stream=_StaticAsyncByteStream(
                [b'event: response.completed\ndata: {"type":"response.completed"}\n\n']
            ),
            headers={"Content-Type": "text/event-stream"},
        )

    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(
        endpoints.responses_client,
        "transport",
        httpx.MockTransport(upstream_handler),
    )

    response = TestClient(app).post(
        "/v1/responses",
        content=raw_body,
        headers={
            "X-Client-Task-Id": "stream-task-original",
            "X-Client-Trace-Id": "stream-trace-original",
            "Accept-Encoding": "identity",
        },
    )

    headers = captured["headers"]
    assert response.status_code == 200
    assert captured["body"] == raw_body
    assert headers["authorization"] == "Bearer fake-package-key"
    assert headers["x-api-key"] == "fake-package-key"
    assert headers["x-ai-forward-url"] == "https://llm.api.zyuncs.com/v1"
    assert headers["x-pkg-model"] == "2048"
    assert headers["x-ai-forward-email"] == "tester@example.test"
    assert headers["x-client-task-id"] == "stream-task-original"
    assert headers["x-client-trace-id"] == "stream-trace-original"


def test_stream_preserves_error_status_gzip_and_duplicate_headers(
    monkeypatch, tmp_path
):
    """流式上游的错误状态、压缩字节和重复端到端 Header 必须原样交付。"""
    import src.api.endpoints as endpoints

    _install_empty_catalog(monkeypatch, tmp_path)
    compressed_body = gzip.compress(
        b'event: response.failed\ndata: {"type":"response.failed"}\n\n'
    )
    response_stream = _StaticAsyncByteStream(
        [compressed_body[:7], compressed_body[7:]]
    )

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            stream=response_stream,
            headers=[
                (b"Content-Type", b"text/event-stream"),
                (b"Content-Encoding", b"gzip"),
                (b"Set-Cookie", b"first=1"),
                (b"Set-Cookie", b"second=2"),
                (b"Retry-After", b"9"),
                (b"Connection", b"X-Remove-Me"),
                (b"X-Remove-Me", b"hidden"),
                (b"Content-Length", b"999"),
            ],
        )

    transport = _TrackingMockTransport(upstream_handler)
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(endpoints.responses_client, "transport", transport)

    status_code, raw_headers, response_body = asyncio.run(
        _call_stream_app_directly(
            b'{"model":"ordinary/model","input":"hello","stream":true}',
            headers=[(b"accept-encoding", b"gzip")],
        )
    )

    normalized_headers = [(name.lower(), value) for name, value in raw_headers]
    header_names = {name for name, _ in normalized_headers}
    assert status_code == 429
    assert response_body == compressed_body
    assert (b"content-encoding", b"gzip") in normalized_headers
    assert (b"retry-after", b"9") in normalized_headers
    assert [
        value for name, value in normalized_headers if name == b"set-cookie"
    ] == [b"first=1", b"second=2"]
    assert b"connection" not in header_names
    assert b"x-remove-me" not in header_names
    assert b"content-length" not in header_names
    assert response_stream.close_count == 1
    assert transport.close_count == 1
    assert endpoints.responses_client.active_requests == set()


def test_stream_failure_after_response_start_only_truncates_and_cleans_up(
    monkeypatch, tmp_path, caplog
):
    """下游已开始后的上游异常只能截断 body，不能注入事件或记录敏感详情。"""
    import src.api.endpoints as endpoints

    _install_empty_catalog(monkeypatch, tmp_path)
    partial_body = b'event: response.output_text.delta\ndata: {"delta":"A"}\n\n'
    secret_detail = "private-upstream-body-and-fake-ordinary-key"
    response_stream = _FailingAsyncByteStream(
        httpx.ReadError(secret_detail),
        first_chunk=partial_body,
    )

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=response_stream,
            headers={"Content-Type": "text/event-stream"},
        )

    transport = _TrackingMockTransport(upstream_handler)
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(endpoints.responses_client, "transport", transport)
    caplog.set_level(logging.INFO)

    status_code, _, response_body = asyncio.run(
        _call_stream_app_directly(
            b'{"model":"ordinary/model","input":"hello","stream":true}'
        )
    )

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert status_code == 200
    assert response_body == partial_body
    assert b"[DONE]" not in response_body
    assert b"response.failed" not in response_body
    assert "responses_stream_interrupted" in log_text
    assert "ReadError" in log_text
    assert secret_detail not in log_text
    assert "fake-ordinary-key" not in log_text
    assert response_stream.close_count == 1
    assert transport.close_count == 1
    assert endpoints.responses_client.active_requests == set()


def test_cancelling_asgi_task_while_connecting_propagates_and_cleans_up(
    monkeypatch, tmp_path
):
    """等待上游响应头时取消 ASGI task，应立即取消连接且不依赖释放 gate。"""
    import src.api.endpoints as endpoints

    _install_empty_catalog(monkeypatch, tmp_path)
    connection_started = asyncio.Event()
    connection_cancelled = asyncio.Event()

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        connection_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            connection_cancelled.set()
            raise

    transport = _TrackingMockTransport(upstream_handler)
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(endpoints.responses_client, "transport", transport)

    async def exercise_cancel():
        received_request = False

        async def receive():
            nonlocal received_request
            if not received_request:
                received_request = True
                return {
                    "type": "http.request",
                    "body": b'{"model":"ordinary/model","input":"hello","stream":true}',
                    "more_body": False,
                }
            await asyncio.Event().wait()

        async def send(message):
            raise AssertionError("连接完成前不应开始下游响应")

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/responses",
            "raw_path": b"/v1/responses",
            "query_string": b"",
            "headers": [(b"host", b"local.test")],
            "client": ("127.0.0.1", 12345),
            "server": ("local.test", 80),
            "root_path": "",
            "state": {},
        }
        app_task = asyncio.create_task(app(scope, receive, send))
        await asyncio.wait_for(connection_started.wait(), timeout=1)
        app_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(app_task, timeout=1)

    asyncio.run(exercise_cancel())

    assert connection_cancelled.is_set()
    assert transport.close_count == 1
    assert endpoints.responses_client.active_requests == set()


def test_cancelling_asgi_task_while_reading_stream_propagates_and_cleans_up(
    monkeypatch, tmp_path
):
    """raw body 正在等待时取消 ASGI task，应取消 iterator 并完整释放上游资源。"""
    import src.api.endpoints as endpoints

    _install_empty_catalog(monkeypatch, tmp_path)
    response_stream = _GatedAsyncByteStream()

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=response_stream,
            headers={"Content-Type": "text/event-stream"},
        )

    transport = _TrackingMockTransport(upstream_handler)
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(endpoints.responses_client, "transport", transport)

    async def exercise_cancel():
        received_request = False
        sent_messages = []

        async def receive():
            nonlocal received_request
            if not received_request:
                received_request = True
                return {
                    "type": "http.request",
                    "body": b'{"model":"ordinary/model","input":"hello","stream":true}',
                    "more_body": False,
                }
            await asyncio.Event().wait()

        async def send(message):
            sent_messages.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/responses",
            "raw_path": b"/v1/responses",
            "query_string": b"",
            "headers": [(b"host", b"local.test")],
            "client": ("127.0.0.1", 12345),
            "server": ("local.test", 80),
            "root_path": "",
            "state": {},
        }
        app_task = asyncio.create_task(app(scope, receive, send))
        await asyncio.wait_for(response_stream.read_started.wait(), timeout=1)
        assert any(
            message["type"] == "http.response.start" for message in sent_messages
        )
        app_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(app_task, timeout=1)

    asyncio.run(exercise_cancel())

    assert response_stream.read_cancelled.is_set()
    assert response_stream.close_count == 1
    assert transport.close_count == 1
    assert endpoints.responses_client.active_requests == set()


def test_http_disconnect_stops_blocked_upstream_stream_without_releasing_gate(
    monkeypatch, tmp_path
):
    """客户端断开时，阻塞中的上游读取必须立即取消并只清理一次。"""
    import src.api.endpoints as endpoints

    _install_empty_catalog(monkeypatch, tmp_path)
    response_stream = _GatedAsyncByteStream()
    disconnect = asyncio.Event()

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=response_stream,
            headers={"Content-Type": "text/event-stream"},
        )

    transport = _TrackingMockTransport(upstream_handler)
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(endpoints.responses_client, "transport", transport)

    async def exercise_disconnect():
        received_request = False

        async def receive():
            nonlocal received_request
            if not received_request:
                received_request = True
                return {
                    "type": "http.request",
                    "body": b'{"model":"ordinary/model","input":"hello","stream":true}',
                    "more_body": False,
                }
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            return None

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/responses",
            "raw_path": b"/v1/responses",
            "query_string": b"",
            "headers": [(b"host", b"local.test")],
            "client": ("127.0.0.1", 12345),
            "server": ("local.test", 80),
            "root_path": "",
            "state": {},
        }
        app_task = asyncio.create_task(app(scope, receive, send))
        await asyncio.wait_for(response_stream.read_started.wait(), timeout=1)
        disconnect.set()
        await asyncio.wait_for(app_task, timeout=1)

    asyncio.run(exercise_disconnect())

    assert response_stream.read_cancelled.is_set()
    assert response_stream.close_count == 1
    assert transport.close_count == 1
    assert endpoints.responses_client.active_requests == set()


def test_downstream_send_failure_stops_stream_and_closes_resources_once(
    monkeypatch, tmp_path
):
    """下游写入失败后不得继续拉取上游下一块，并且所有资源只关闭一次。"""
    import src.api.endpoints as endpoints

    _install_empty_catalog(monkeypatch, tmp_path)
    response_stream = _GatedAsyncByteStream(first_chunk=b"first-native-sse-chunk")

    async def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=response_stream,
            headers={"Content-Type": "text/event-stream"},
        )

    transport = _TrackingMockTransport(upstream_handler)
    monkeypatch.setattr(endpoints.responses_client, "base_url", "https://proxy.example.test")
    monkeypatch.setattr(endpoints.responses_client, "api_key", "fake-ordinary-key")
    monkeypatch.setattr(endpoints.responses_client, "transport", transport)

    async def exercise_send_failure():
        received_request = False

        async def receive():
            nonlocal received_request
            if not received_request:
                received_request = True
                return {
                    "type": "http.request",
                    "body": b'{"model":"ordinary/model","input":"hello","stream":true}',
                    "more_body": False,
                }
            await asyncio.Event().wait()

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                raise OSError("simulated downstream disconnect")

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/responses",
            "raw_path": b"/v1/responses",
            "query_string": b"",
            "headers": [(b"host", b"local.test")],
            "client": ("127.0.0.1", 12345),
            "server": ("local.test", 80),
            "root_path": "",
            "state": {},
        }
        with pytest.raises(ClientDisconnect):
            await asyncio.wait_for(app(scope, receive, send), timeout=1)

    asyncio.run(exercise_send_failure())

    assert not response_stream.read_started.is_set()
    assert response_stream.close_count == 1
    assert transport.close_count == 1
    assert endpoints.responses_client.active_requests == set()
