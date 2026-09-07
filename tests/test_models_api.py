"""OpenAI Models 公开入口的端到端行为测试。"""

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from src.core.models_catalog import (
    ModelCatalogService,
    WisCodeSettingsModelSource,
    ZqiPackageModelSource,
)
from src.core.wiscode_auth import WisCodeAuthProvider
from src.core.zqi_catalog import ZqiCatalogClient
from src.main import app


def _write_fake_auth(tmp_path: Path) -> Path:
    """写入只供 MockTransport 使用的虚假认证，避免读取用户真实凭据。"""
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "host": "catalog.example.test",
                "access_token": "fake-access-token",
                "mail": "tester@example.test",
            }
        ),
        encoding="utf-8",
    )
    return auth_path


def _install_model_catalog(monkeypatch, tmp_path: Path, handler) -> None:
    """安装使用真实来源适配器的模型目录，仅替换外部 HTTP transport。"""
    import src.api.endpoints as endpoints

    auth_provider = WisCodeAuthProvider(_write_fake_auth(tmp_path))
    transport = httpx.MockTransport(handler)
    package_catalog = ZqiCatalogClient(
        auth_provider=auth_provider,
        transport=transport,
    )
    service = ModelCatalogService(
        WisCodeSettingsModelSource(auth_provider, transport=transport),
        ZqiPackageModelSource(package_catalog),
    )
    monkeypatch.setattr(endpoints, "model_catalog_service", service)


def test_models_returns_only_wiscode_settings_groups_when_package_source_fails(
    monkeypatch, tmp_path
):
    """第一来源成功即返回 200，但只收录两个 WisCode 分组的 proxyName。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/llm/config":
            return httpx.Response(
                200,
                json={
                    "context": {"code": 0},
                    "data": {
                        "auto": {
                            "modelList": [
                                {"proxyName": "ignored/auto", "apiType": "chat"}
                            ]
                        },
                        "intranet": {
                            "modelList": [
                                {"proxyName": "ignored/intranet", "apiType": "chat"}
                            ]
                        },
                        "extranet": {
                            "modelList": [
                                {"proxyName": "ignored/extranet", "apiType": "responses"}
                            ]
                        },
                        "intranet-wiscode": {
                            "modelList": [
                                {"proxyName": "wiscode/internal", "apiType": "chat"},
                                {"proxyName": " shared/model ", "apiType": "responses"},
                            ]
                        },
                        "extranet-wiscode": {
                            "modelList": [
                                {"proxyName": "wiscode/external", "apiType": "chat"},
                                {"proxyName": "shared/model", "apiType": "messages"},
                                {"proxyName": "  ", "apiType": "responses"},
                            ]
                        }
                    },
                },
            )
        return httpx.Response(503, json={"context": {"code": 5000}})

    _install_model_catalog(monkeypatch, tmp_path, handler)
    response = TestClient(app).get("/v1/models")

    assert response.status_code == 200
    assert response.json() == {
        "object": "list",
        "data": [
            {
                "id": "wiscode/internal",
                "object": "model",
                "created": 1704067200,
                "owned_by": "wiscode",
            },
            {
                "id": "shared/model",
                "object": "model",
                "created": 1704067200,
                "owned_by": "wiscode",
            },
            {
                "id": "wiscode/external",
                "object": "model",
                "created": 1704067200,
                "owned_by": "wiscode",
            },
        ],
    }


def test_models_returns_only_available_responses_packages_when_settings_source_fails(
    monkeypatch, tmp_path
):
    """第二来源成功即返回 200，并沿用套餐的协议与可用状态过滤。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/llm/config":
            return httpx.Response(503, json={"context": {"code": 5000}})
        return httpx.Response(
            200,
            json={
                "context": {"code": 0},
                "data": {
                    "list": [
                        {
                            "id": 1,
                            "identifier": "zyzj_package",
                            "expireAt": "2999-01-01T00:00:00+00:00",
                            "exhausted": False,
                            "apiKey": {"full": "fake-package-key"},
                            "models": [
                                {
                                    "name": "responses/model",
                                    "apiNames": ["responses"],
                                    "enabled": True,
                                },
                                {
                                    "name": "chat/model",
                                    "apiNames": ["chat"],
                                    "enabled": True,
                                },
                                {
                                    "name": "disabled/model",
                                    "apiNames": ["responses"],
                                    "enabled": False,
                                },
                            ],
                        },
                        {
                            "id": 2,
                            "identifier": "sfdj_package",
                            "expireAt": "2999-01-01T00:00:00+00:00",
                            "exhausted": True,
                            "apiKey": {"full": "fake-exhausted-key"},
                            "models": [
                                {
                                    "name": "exhausted/model",
                                    "apiNames": ["responses"],
                                    "enabled": True,
                                }
                            ],
                        },
                    ]
                },
            },
        )

    _install_model_catalog(monkeypatch, tmp_path, handler)
    response = TestClient(app).get("/v1/models")

    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "id": "responses/model",
            "object": "model",
            "created": 1704067200,
            "owned_by": "zqi",
        }
    ]


def test_models_merges_both_sources_with_settings_order_and_exact_deduplication(
    monkeypatch, tmp_path
):
    """两个来源成功时按第一来源优先合并，并只对精确同名项去重。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/llm/config":
            return httpx.Response(
                200,
                json={
                    "context": {"code": 0},
                    "data": {
                        "extranet-wiscode": {
                            "modelList": [
                                {"proxyName": "shared/model", "apiType": "chat"},
                                {"proxyName": "Settings/Model", "apiType": "messages"},
                            ]
                        }
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "context": {"code": 0},
                "data": {
                    "list": [
                        {
                            "id": 1,
                            "identifier": "zyzj_package",
                            "expireAt": "2999-01-01T00:00:00+00:00",
                            "exhausted": False,
                            "apiKey": {"full": "must-not-leak"},
                            "models": [
                                {
                                    "name": "shared/model",
                                    "apiNames": ["responses"],
                                    "enabled": True,
                                },
                                {
                                    "name": "settings/model",
                                    "apiNames": ["responses"],
                                    "enabled": True,
                                },
                                {
                                    "name": "package/model",
                                    "apiNames": ["responses"],
                                    "enabled": True,
                                },
                            ],
                        }
                    ]
                },
            },
        )

    _install_model_catalog(monkeypatch, tmp_path, handler)
    response = TestClient(app).get("/v1/models")

    assert response.status_code == 200
    assert [
        (item["id"], item["owned_by"]) for item in response.json()["data"]
    ] == [
        ("shared/model", "wiscode"),
        ("Settings/Model", "wiscode"),
        ("settings/model", "zqi"),
        ("package/model", "zqi"),
    ]
    assert "must-not-leak" not in response.text
    assert "fake-access-token" not in response.text


def test_models_returns_502_only_when_both_sources_fail(monkeypatch, tmp_path):
    """两个来源均失败时才把聚合失败暴露为 502。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"context": {"code": 5000}})

    _install_model_catalog(monkeypatch, tmp_path, handler)
    response = TestClient(app).get("/v1/models")

    assert response.status_code == 502
    assert response.json() == {"detail": "模型列表的两个上游来源均不可用"}


def test_models_uses_same_proxy_api_key_authentication(monkeypatch):
    """Models 入口与 Responses 入口共用代理自身的访问密钥。"""
    import src.api.endpoints as endpoints

    monkeypatch.setattr(endpoints.config, "client_api_key", "proxy-secret")
    response = TestClient(app).get("/v1/models")

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid API key"}
