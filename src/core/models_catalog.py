"""聚合 WisCode 配置与智企套餐的 OpenAI 模型列表。"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, List, Optional, Protocol, Sequence

import httpx
from fastapi import HTTPException

from src.core.constants import Constants
from src.core.wiscode_auth import WisCodeAuthProvider
from src.core.zqi_catalog import ZqiCatalogClient, ZqiRouteResolver


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CatalogModel:
    """聚合后保留模型名称及其首次命中来源的所有者。"""

    id: str
    owned_by: str


class ModelNameSource(Protocol):
    """单个模型来源需要提供的最小公开能力。"""

    async def list_model_names(self) -> List[str]:
        """返回该来源当前可见的模型名称。"""


class WisCodeSettingsModelSource:
    """读取 WisCode 官方配置中两个 WisCode 专属分组的 proxyName。"""

    def __init__(
        self,
        auth_provider: WisCodeAuthProvider,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.auth_provider = auth_provider
        self.transport = transport

    async def list_model_names(self) -> List[str]:
        """请求真实配置上游；目标分组内不依据 apiType 或多模态字段过滤。"""
        auth = self.auth_provider.read()
        url = f"https://{auth.host}{Constants.WISCODE_MODEL_CONFIG_PATH}"
        try:
            async with httpx.AsyncClient(
                timeout=Constants.MODELS_SOURCE_TIMEOUT_SECONDS,
                transport=self.transport,
            ) as client:
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {auth.access_token}"},
                )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise HTTPException(
                status_code=502,
                detail="WisCode 模型配置请求失败：网络连接或超时",
            ) from exc

        if response.status_code == 401:
            raise HTTPException(
                status_code=401,
                detail="WisCode 模型配置认证失败：access_token 无效或已过期",
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise HTTPException(
                status_code=502,
                detail=f"WisCode 模型配置请求失败：HTTP {response.status_code}",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=502,
                detail="WisCode 模型配置响应不是合法 JSON",
            ) from exc
        return self._extract_proxy_names(payload)

    @staticmethod
    def _extract_proxy_names(payload: Any) -> List[str]:
        """从所有配置分组提取非空 proxyName，保留上游出现顺序。"""
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=502,
                detail="WisCode 模型配置响应顶层类型错误：期望 object",
            )
        context = payload.get("context")
        if not isinstance(context, dict) or context.get("code") != 0:
            raise HTTPException(
                status_code=502,
                detail="WisCode 模型配置响应 context.code 非成功值",
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise HTTPException(
                status_code=502,
                detail="WisCode 模型配置响应 data 字段类型错误：期望 object",
            )

        names: List[str] = []
        for group_name in Constants.WISCODE_MODEL_CONFIG_GROUPS:
            # 第一来源只公开 WisCode 专属分组；其他配置分组由产品要求明确排除。
            group = data.get(group_name)
            if not isinstance(group, dict) or "modelList" not in group:
                continue
            model_list = group.get("modelList")
            if not isinstance(model_list, list):
                raise HTTPException(
                    status_code=502,
                    detail="WisCode 模型配置 modelList 字段类型错误：期望 array",
                )
            for model in model_list:
                if not isinstance(model, dict):
                    continue
                proxy_name = model.get("proxyName")
                if isinstance(proxy_name, str) and proxy_name.strip():
                    names.append(proxy_name.strip())
        return names


class ZqiPackageModelSource:
    """从现有智企套餐目录读取当前可用于 Responses 的模型。"""

    def __init__(self, catalog: ZqiCatalogClient) -> None:
        self.resolver = ZqiRouteResolver(catalog)

    async def list_model_names(self) -> List[str]:
        """复用套餐路由的协议、有效期、额度和启用状态判断。"""
        return await self.resolver.list_available_models()


class ModelCatalogService:
    """并发读取两个独立来源，并按部分成功规则合并名称。"""

    def __init__(
        self,
        settings_source: ModelNameSource,
        package_source: ModelNameSource,
    ) -> None:
        self.sources: Sequence[tuple[str, str, ModelNameSource]] = (
            (
                Constants.MODELS_SOURCE_SETTINGS,
                Constants.MODELS_OWNER_WISCODE,
                settings_source,
            ),
            (
                Constants.MODELS_SOURCE_PACKAGES,
                Constants.MODELS_OWNER_ZQI,
                package_source,
            ),
        )

    async def list_models(self) -> List[CatalogModel]:
        """任一来源成功即返回带来源的去重结果；全部失败时返回 502。"""
        results = await asyncio.gather(
            *(source.list_model_names() for _, _, source in self.sources),
            return_exceptions=True,
        )
        successful_results: List[tuple[str, List[str]]] = []
        for (source_name, owner, _), result in zip(self.sources, results):
            if isinstance(result, Exception):
                # 只记录异常类型，避免第三方响应或认证信息进入日志。
                logger.warning(
                    "models_source_failed source=%s exception_type=%s",
                    source_name,
                    type(result).__name__,
                )
                continue
            logger.info(
                "models_source_succeeded source=%s count=%s",
                source_name,
                len(result),
            )
            successful_results.append((owner, result))

        if not successful_results:
            raise HTTPException(
                status_code=502,
                detail=Constants.MODELS_ALL_SOURCES_FAILED_DETAIL,
            )
        return _stable_unique_models(successful_results)


def _stable_unique_models(
    groups: Sequence[tuple[str, Sequence[str]]],
) -> List[CatalogModel]:
    """按来源顺序去重，并保留模型首次出现时对应的所有者。"""
    seen = set()
    unique_models: List[CatalogModel] = []
    for owner, group in groups:
        for raw_name in group:
            name = raw_name.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            unique_models.append(CatalogModel(id=name, owned_by=owner))
    return unique_models
