"""WisCode 智企套餐目录读取、缓存与请求级路由。"""

import asyncio
import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

import httpx
from fastapi import HTTPException

from src.core.constants import Constants

_HOST_PATTERN = re.compile(r"^[A-Za-z0-9.-]+(?::[0-9]{1,5})?$")


@dataclass(frozen=True)
class ZqiAuth:
    """读取后的 WisCode 认证信息。"""

    host: str
    access_token: str
    mail: Optional[str]


@dataclass(frozen=True)
class ZqiSnapshot:
    """不可变语义上的目录快照。"""

    packages: Tuple[Mapping[str, Any], ...]
    loaded_at: datetime
    auth_fingerprint: str
    auth: ZqiAuth


@dataclass(frozen=True)
class ZqiRoute:
    """单次上游请求使用的不可变路由。"""

    model: str
    api_key: Optional[str]
    headers: Mapping[str, str]


class ZqiCatalogClient:
    """读取并缓存智企套餐目录。"""

    def __init__(
        self,
        auth_path: Optional[Path] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.auth_path = auth_path or Path.home() / ".wiscode" / "auth.json"
        self.transport = transport
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.snapshot: Optional[ZqiSnapshot] = None
        self.inflight: Dict[str, asyncio.Task[ZqiSnapshot]] = {}

    async def get_snapshot(self) -> ZqiSnapshot:
        """读取当前认证上下文对应的目录快照。"""
        auth = self._read_auth()
        fingerprint = self._fingerprint(auth)
        current = self.snapshot
        if (
            current
            and current.auth_fingerprint == fingerprint
            and (self.now() - current.loaded_at).total_seconds() < Constants.ZQI_CACHE_TTL_SECONDS
        ):
            return current

        task = self.inflight.get(fingerprint)
        if task is None:
            task = asyncio.create_task(self._refresh(auth, fingerprint))
            self.inflight[fingerprint] = task
            task.add_done_callback(
                lambda completed: self._clear_inflight_task(fingerprint, completed)
            )
        try:
            snapshot = await task
        except Exception:
            if self.snapshot and self.snapshot.auth_fingerprint == fingerprint:
                self.snapshot = None
            raise
        current_auth = self._read_auth()
        current_fingerprint = self._fingerprint(current_auth)
        if fingerprint != current_fingerprint:
            return await self.get_snapshot()
        self.snapshot = snapshot
        return snapshot

    def _read_auth(self) -> ZqiAuth:
        """读取并校验 auth.json 的必要字段。"""
        try:
            raw = json.loads(self.auth_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail="无法读取 ~/.wiscode/auth.json：文件不存在") from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail="无法读取 ~/.wiscode/auth.json：文件读取失败") from exc
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail="~/.wiscode/auth.json JSON 解析失败") from exc
        if not isinstance(raw, dict):
            raise HTTPException(status_code=500, detail="~/.wiscode/auth.json 顶层类型错误：期望 object")

        host = raw.get("host")
        if not isinstance(host, str) or not host.strip() or not _HOST_PATTERN.fullmatch(host.strip()):
            raise HTTPException(status_code=500, detail="~/.wiscode/auth.json.host 字段非法：期望 hostname 或 hostname:port")
        token = raw.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise HTTPException(status_code=401, detail="~/.wiscode/auth.json.access_token 缺失或无效")
        mail = raw.get("mail")
        return ZqiAuth(host.strip(), token.strip(), mail if isinstance(mail, str) else None)

    def _clear_inflight_task(self, fingerprint: str, completed: asyncio.Task[ZqiSnapshot]) -> None:
        """仅清理仍指向当前任务的 single-flight 记录。"""
        if self.inflight.get(fingerprint) is completed:
            self.inflight.pop(fingerprint, None)

    async def _refresh(self, auth: ZqiAuth, fingerprint: str) -> ZqiSnapshot:
        """请求并完整校验新的目录快照。"""
        url = (
            f"https://{auth.host}/api/zqi/model-packages"
            "?include_limit=true&include_keys=true&include_models=true"
        )
        last_error: Optional[Exception] = None
        for attempt in range(Constants.ZQI_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=Constants.ZQI_REQUEST_TIMEOUT_SECONDS, transport=self.transport
                ) as client:
                    response = await client.get(url, headers={"Authorization": f"Bearer {auth.access_token}"})
                if response.status_code == 401:
                    raise HTTPException(status_code=401, detail="智企套餐目录认证失败：access_token 无效或已过期")
                if response.status_code < 200 or response.status_code >= 300:
                    raise HTTPException(status_code=502, detail=f"智企套餐目录请求失败：HTTP {response.status_code}")
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise HTTPException(status_code=502, detail="智企套餐目录响应不是合法 JSON") from exc
                packages = self._validate_payload(payload)
                return ZqiSnapshot(packages, self.now(), fingerprint, auth)
            except HTTPException:
                raise
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                last_error = exc
                if attempt < Constants.ZQI_MAX_RETRIES:
                    await asyncio.sleep(Constants.ZQI_RETRY_BACKOFF_SECONDS)
                    continue
                raise HTTPException(status_code=502, detail="智企套餐目录请求失败：网络连接或超时，已尝试 2 次") from exc
        raise HTTPException(status_code=502, detail=f"智企套餐目录请求失败：{last_error}")

    def _validate_payload(self, payload: Any) -> Tuple[Mapping[str, Any], ...]:
        """校验目录响应中影响路由的字段。"""
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail="智企套餐目录响应顶层类型错误：期望 object")
        context = payload.get("context")
        if not isinstance(context, dict):
            raise HTTPException(status_code=502, detail="智企套餐目录响应 context 字段类型错误：期望 object")
        context_code = context.get("code")
        if isinstance(context_code, bool) or context_code != 0:
            raise HTTPException(status_code=502, detail=f"智企套餐目录 context.code 非成功值：{context.get('code')}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise HTTPException(status_code=502, detail="智企套餐目录响应 data 字段类型错误：期望 object")
        packages = data.get("list")
        if not isinstance(packages, list):
            raise HTTPException(status_code=502, detail="智企套餐目录响应 data.list 字段类型错误：期望 array")
        for package_index, item in enumerate(packages):
            self._validate_package(item, package_index)
        return tuple(self._freeze(item) for item in copy.deepcopy(packages))

    @classmethod
    def _freeze(cls, value: Any) -> Any:
        """递归冻结目录数据，避免请求间共享的快照被意外改写。"""
        if isinstance(value, dict):
            return MappingProxyType({key: cls._freeze(item) for key, item in value.items()})
        if isinstance(value, list):
            return tuple(cls._freeze(item) for item in value)
        return value

    def _validate_package(self, item: Any, package_index: int) -> None:
        """校验单个套餐的路由字段。"""
        prefix = f"data.list[{package_index}]"
        if not isinstance(item, dict):
            raise HTTPException(status_code=502, detail=f"{prefix} 类型错误：期望 object")
        identifier = item.get("identifier")
        if not isinstance(identifier, str) or not identifier.strip():
            raise HTTPException(
                status_code=502,
                detail=f"{prefix}.identifier 缺失、为空或类型错误：期望非空 string",
            )
        if "id" not in item or item["id"] is None or not self._header_value(item["id"]):
            raise HTTPException(status_code=502, detail=f"{prefix}.id 缺失或无法转换为单值 Header")
        expire_at = item.get("expireAt")
        if not isinstance(expire_at, str) or not expire_at.strip():
            raise HTTPException(status_code=502, detail=f"{prefix}.expireAt 字段缺失：期望 ISO 8601 时间字符串")
        try:
            _parse_expire_at(expire_at)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=f"{prefix}.expireAt 无法解析为 ISO 8601 时间：{expire_at}") from exc
        except HTTPException as exc:
            raise HTTPException(status_code=502, detail=f"{prefix}.expireAt 无法解析为带时区的 ISO 8601 时间") from exc
        exhausted = item.get("exhausted")
        if exhausted is not None and not isinstance(exhausted, bool):
            raise HTTPException(status_code=502, detail=f"{prefix}.exhausted 类型错误：期望 boolean 或 null")
        api_key = item.get("apiKey")
        if not isinstance(api_key, dict) or not isinstance(api_key.get("full"), str) or not api_key["full"].strip():
            raise HTTPException(status_code=502, detail=f"{prefix}.apiKey.full 缺失、为空或类型错误：期望非空 string")
        models = item.get("models")
        if not isinstance(models, list):
            raise HTTPException(status_code=502, detail=f"{prefix}.models 字段类型错误：期望 array")
        seen = set()
        for model_index, model in enumerate(models):
            model_prefix = f"{prefix}.models[{model_index}]"
            if not isinstance(model, dict):
                raise HTTPException(status_code=502, detail=f"{model_prefix} 类型错误：期望 object")
            name = model.get("name")
            if not isinstance(name, str) or not name:
                raise HTTPException(status_code=502, detail=f"{model_prefix}.name 缺失或类型错误：期望 string")
            if name in seen:
                raise HTTPException(status_code=502, detail=f"{prefix} 重复模型名：{name}，路径 {model_prefix}")
            seen.add(name)
            api_names = model.get("apiNames")
            if not isinstance(api_names, list) or any(not isinstance(value, str) for value in api_names):
                raise HTTPException(status_code=502, detail=f"{model_prefix}.apiNames 类型错误：期望 string array")
            enabled = model.get("enabled")
            if enabled is not None and not isinstance(enabled, bool):
                raise HTTPException(status_code=502, detail=f"{model_prefix}.enabled 类型错误：期望 boolean 或 null")

    @staticmethod
    def _header_value(value: Any) -> Optional[str]:
        """将套餐 ID 转换为单值 Header 字符串。"""
        if isinstance(value, (dict, list, tuple, set)):
            return None
        text = str(value)
        return text if text and "\r" not in text and "\n" not in text else None

    @staticmethod
    def _fingerprint(auth: ZqiAuth) -> str:
        """生成不包含明文 token 的认证指纹。"""
        return hashlib.sha256(f"{auth.host}\0{auth.access_token}".encode()).hexdigest()


class ZqiRouteResolver:
    """根据目录快照解析单次模型请求 route。"""

    def __init__(self, catalog: ZqiCatalogClient) -> None:
        self.catalog = catalog

    async def resolve(self, model: str) -> ZqiRoute:
        """解析完整模型名对应的普通或套餐路由。"""
        snapshot = await self.catalog.get_snapshot()
        matches = [
            (package, model_item)
            for package in snapshot.packages
            for model_item in package["models"]
            if model_item["name"] == model
        ]
        if not matches:
            return ZqiRoute(model, None, {})

        messages_matches = [item for item in matches if "messages" in item[1]["apiNames"]]
        if not messages_matches:
            raise HTTPException(status_code=400, detail=f"模型 {model} 不支持 messages 协议")

        now = self.catalog.now()
        reasons = []
        selected = self._select_package(messages_matches, now, reasons)
        if selected is None:
            reason = "、".join(dict.fromkeys(reasons)) or "没有可用套餐"
            raise HTTPException(
                status_code=503,
                detail=f"模型 {model} 在智企套餐中存在，但当前不可用：{reason}",
            )
        package, _ = selected
        auth = snapshot.auth
        headers = {
            "X-Ai-Forward-Url": Constants.ZQI_FORWARD_URL,
            "X-Pkg-Model": self.catalog._header_value(package["id"]) or "",
        }
        mail = auth.mail.strip() if auth.mail else ""
        if mail and len(mail) <= 320 and "\r" not in mail and "\n" not in mail and "@" in mail:
            headers["X-Ai-Forward-Email"] = mail
        return ZqiRoute(model, package["apiKey"]["full"], headers)

    def _select_package(
        self,
        matches: List[Tuple[Mapping[str, Any], Mapping[str, Any]]],
        now: datetime,
        reasons: List[str],
    ) -> Optional[Tuple[Mapping[str, Any], Mapping[str, Any]]]:
        """按内网、外网、未知类型的顺序选择第一个可用套餐。"""
        priority_identifiers = (
            Constants.ZQI_INTERNAL_IDENTIFIER,
            Constants.ZQI_EXTERNAL_IDENTIFIER,
        )
        for identifier in priority_identifiers:
            selected = self._select_from_identifier(
                matches, identifier, now, reasons
            )
            if selected is not None:
                return selected

        unknown_matches = [
            match
            for match in matches
            if match[0]["identifier"] not in priority_identifiers
        ]
        return self._select_from_matches(unknown_matches, now, reasons)

    def _select_from_identifier(
        self,
        matches: List[Tuple[Mapping[str, Any], Mapping[str, Any]]],
        identifier: str,
        now: datetime,
        reasons: List[str],
    ) -> Optional[Tuple[Mapping[str, Any], Mapping[str, Any]]]:
        """按目录顺序检查指定 identifier 下的套餐。"""
        grouped_matches = [
            match for match in matches if match[0]["identifier"] == identifier
        ]
        return self._select_from_matches(grouped_matches, now, reasons)

    @staticmethod
    def _select_from_matches(
        matches: List[Tuple[Mapping[str, Any], Mapping[str, Any]]],
        now: datetime,
        reasons: List[str],
    ) -> Optional[Tuple[Mapping[str, Any], Mapping[str, Any]]]:
        """按目录顺序返回第一个满足状态条件的套餐。"""
        for package, model_item in matches:
            expire_at = _parse_expire_at(package["expireAt"])
            if expire_at <= now:
                reasons.append("套餐已过期")
                continue
            if package.get("exhausted") is True:
                reasons.append("套餐额度已耗尽")
                continue
            if model_item.get("enabled") is False:
                reasons.append("模型已禁用")
                continue
            return package, model_item
        return None


def _parse_expire_at(value: str) -> datetime:
    """解析有明确时区的 ISO 8601 时间，避免本地时区歧义。"""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise HTTPException(
            status_code=502,
            detail="智企套餐目录 expireAt 无法解析为带时区的 ISO 8601 时间",
        )
    return parsed
