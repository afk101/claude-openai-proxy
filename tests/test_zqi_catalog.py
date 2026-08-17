"""智企目录与请求路由行为测试。"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from src.core.zqi_catalog import ZqiCatalogClient, ZqiRouteResolver


def auth_payload(tmp_path: Path, **overrides):
    payload = {
        "host": "catalog.example.test",
        "access_token": "external-token",
        "mail": "user@example.test",
    }
    payload.update(overrides)
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def package(model_name="pkg/model", package_id=1022, **overrides):
    item = {
        "id": package_id,
        "expireAt": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "exhausted": False,
        "apiKey": {"full": "package-key"},
        "models": [
            {"name": model_name, "apiNames": ["messages"], "enabled": True}
        ],
    }
    item.update(overrides)
    return item


def catalog_response(packages):
    return {"context": {"code": 0, "message": "success"}, "data": {"list": packages}}


def test_loads_catalog_with_required_query_and_bearer(tmp_path):
    async def run():
        auth_path = auth_payload(tmp_path)
        requests = []

        async def handler(request):
            requests.append(request)
            return httpx.Response(200, json=catalog_response([package()]))

        client = ZqiCatalogClient(
            auth_path=auth_path,
            transport=httpx.MockTransport(handler),
            now=lambda: datetime.now(timezone.utc),
        )
        snapshot = await client.get_snapshot()

        assert snapshot.packages[0]["id"] == 1022
        assert requests[0].url.path == "/api/zqi/model-packages"
        assert requests[0].url.params["include_limit"] == "true"
        assert requests[0].url.params["include_keys"] == "true"
        assert requests[0].url.params["include_models"] == "true"
        assert requests[0].headers["authorization"] == "Bearer external-token"

    asyncio.run(run())


def test_caches_for_thirty_minutes_and_refreshes_on_auth_change(tmp_path):
    async def run():
        auth_path = auth_payload(tmp_path)
        requests = []
        current_time = [datetime.now(timezone.utc)]

        async def handler(request):
            requests.append(request)
            return httpx.Response(200, json=catalog_response([package()]))

        client = ZqiCatalogClient(
            auth_path=auth_path,
            transport=httpx.MockTransport(handler),
            now=lambda: current_time[0],
        )
        await client.get_snapshot()
        await client.get_snapshot()
        assert len(requests) == 1

        current_time[0] += timedelta(minutes=31)
        await client.get_snapshot()
        assert len(requests) == 2

        auth_path.write_text(
            json.dumps({"host": "other.example.test", "access_token": "new-token"}),
            encoding="utf-8",
        )
        await client.get_snapshot()
        assert len(requests) == 3

    asyncio.run(run())


def test_resolves_package_route_and_preserves_model_name(tmp_path):
    async def run():
        auth_path = auth_payload(tmp_path)
        async def handler(request):
            return httpx.Response(200, json=catalog_response([package("pkg/model")]))

        catalog = ZqiCatalogClient(auth_path=auth_path, transport=httpx.MockTransport(handler))
        route = await ZqiRouteResolver(catalog).resolve("pkg/model")

        assert route.api_key == "package-key"
        assert route.headers["X-Ai-Forward-Url"] == "https://llm.api.zyuncs.com/v1"
        assert route.headers["X-Pkg-Model"] == "1022"
        assert route.headers["X-Ai-Forward-Email"] == "user@example.test"
        assert route.model == "pkg/model"

    asyncio.run(run())


def test_unknown_model_uses_default_route(tmp_path):
    async def run():
        auth_path = auth_payload(tmp_path)
        async def handler(request):
            return httpx.Response(200, json=catalog_response([package("other/model")]))

        catalog = ZqiCatalogClient(auth_path=auth_path, transport=httpx.MockTransport(handler))
        route = await ZqiRouteResolver(catalog).resolve("ordinary/model")
        assert route.api_key is None
        assert route.headers == {}

    asyncio.run(run())


@pytest.mark.parametrize(
    ("field", "expected"),
    [("apiNames", "apiNames"), ("expireAt", "expireAt"), ("apiKey", "apiKey.full")],
)
def test_invalid_route_schema_is_explicit(tmp_path, field, expected):
    async def run():
        auth_path = auth_payload(tmp_path)
        item = package("pkg/model")
        if field == "apiNames":
            item["models"][0][field] = "messages"
        elif field == "expireAt":
            item[field] = "invalid-date"
        else:
            item[field] = {}

        async def handler(request):
            return httpx.Response(200, json=catalog_response([item]))

        catalog = ZqiCatalogClient(auth_path=auth_path, transport=httpx.MockTransport(handler))
        with pytest.raises(HTTPException) as error:
            await ZqiRouteResolver(catalog).resolve("pkg/model")
        assert error.value.status_code == 502
        assert expected in str(error.value.detail)

    asyncio.run(run())
