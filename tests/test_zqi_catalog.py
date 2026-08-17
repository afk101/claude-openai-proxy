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


def package(
    model_name="pkg/model",
    package_id=1022,
    identifier="zyzj_package",
    **overrides,
):
    item = {
        "id": package_id,
        "identifier": identifier,
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


def test_concurrent_refresh_uses_single_catalog_request(tmp_path):
    async def run():
        auth_path = auth_payload(tmp_path)
        requests = []
        started = asyncio.Event()
        release = asyncio.Event()

        async def handler(request):
            requests.append(request)
            started.set()
            await release.wait()
            return httpx.Response(200, json=catalog_response([package()]))

        catalog = ZqiCatalogClient(
            auth_path=auth_path,
            transport=httpx.MockTransport(handler),
        )
        first = asyncio.create_task(catalog.get_snapshot())
        second = asyncio.create_task(catalog.get_snapshot())
        await started.wait()
        assert len(requests) == 1
        release.set()
        await asyncio.gather(first, second)

    asyncio.run(run())


def test_catalog_retries_network_failure_once(tmp_path):
    async def run():
        auth_path = auth_payload(tmp_path)
        attempts = []

        async def handler(request):
            attempts.append(request)
            if len(attempts) == 1:
                raise httpx.ConnectError("temporary failure", request=request)
            return httpx.Response(200, json=catalog_response([]))

        catalog = ZqiCatalogClient(
            auth_path=auth_path,
            transport=httpx.MockTransport(handler),
        )
        snapshot = await catalog.get_snapshot()

        assert snapshot.packages == ()
        assert len(attempts) == 2

    asyncio.run(run())


def test_catalog_401_does_not_retry(tmp_path):
    async def run():
        auth_path = auth_payload(tmp_path)
        attempts = []

        async def handler(request):
            attempts.append(request)
            return httpx.Response(401, json={"context": {"code": 401}})

        catalog = ZqiCatalogClient(
            auth_path=auth_path,
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(HTTPException) as error:
            await catalog.get_snapshot()

        assert error.value.status_code == 401
        assert len(attempts) == 1

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


def test_prefers_internal_then_external_then_unknown_identifier(tmp_path):
    async def run():
        auth_path = auth_payload(tmp_path)
        packages = [
            package("pkg/model", 1, "unknown_package"),
            package("pkg/model", 2, "sfdj_package"),
            package("pkg/model", 3, "zyzj_package"),
        ]

        async def handler(request):
            return httpx.Response(200, json=catalog_response(packages))

        catalog = ZqiCatalogClient(auth_path=auth_path, transport=httpx.MockTransport(handler))
        route = await ZqiRouteResolver(catalog).resolve("pkg/model")

        assert route.headers["X-Pkg-Model"] == "3"

    asyncio.run(run())


def test_falls_back_in_identifier_priority_when_higher_priority_is_unavailable(tmp_path):
    async def run():
        auth_path = auth_payload(tmp_path)
        packages = [
            package("pkg/model", 1, "zyzj_package", exhausted=True),
            package("pkg/model", 2, "sfdj_package"),
            package("pkg/model", 3, "unknown_package"),
        ]

        async def handler(request):
            return httpx.Response(200, json=catalog_response(packages))

        catalog = ZqiCatalogClient(auth_path=auth_path, transport=httpx.MockTransport(handler))
        route = await ZqiRouteResolver(catalog).resolve("pkg/model")

        assert route.headers["X-Pkg-Model"] == "2"

    asyncio.run(run())


def test_checks_next_package_within_same_identifier(tmp_path):
    async def run():
        auth_path = auth_payload(tmp_path)
        packages = [
            package("pkg/model", 1, "zyzj_package", exhausted=True),
            package("pkg/model", 2, "zyzj_package"),
            package("pkg/model", 3, "sfdj_package"),
        ]

        async def handler(request):
            return httpx.Response(200, json=catalog_response(packages))

        catalog = ZqiCatalogClient(auth_path=auth_path, transport=httpx.MockTransport(handler))
        route = await ZqiRouteResolver(catalog).resolve("pkg/model")

        assert route.headers["X-Pkg-Model"] == "2"

    asyncio.run(run())


@pytest.mark.parametrize(
    "identifier",
    [None, 123, ""],
)
def test_invalid_identifier_is_explicit(tmp_path, identifier):
    async def run():
        auth_path = auth_payload(tmp_path)
        item = package("pkg/model", identifier=identifier)

        async def handler(request):
            return httpx.Response(200, json=catalog_response([item]))

        catalog = ZqiCatalogClient(auth_path=auth_path, transport=httpx.MockTransport(handler))
        with pytest.raises(HTTPException) as error:
            await ZqiRouteResolver(catalog).resolve("pkg/model")

        assert error.value.status_code == 502
        assert "identifier" in str(error.value.detail)

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
