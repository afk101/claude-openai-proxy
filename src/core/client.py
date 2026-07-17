"""Claude Messages 兼容上游客户端。"""

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Callable, Dict, Optional

import httpx
from fastapi import HTTPException

from src.core.constants import Constants

logger = logging.getLogger(__name__)


class OpenedClaudeStream:
    """持有已连接的 Claude SSE 响应，并负责幂等释放资源。"""

    def __init__(
        self,
        response: httpx.Response,
        http_client: httpx.AsyncClient,
        cancel_event: Optional[asyncio.Event],
        on_close: Callable[[], None],
    ) -> None:
        self.response = response
        self.http_client = http_client
        self.cancel_event = cancel_event
        self.on_close = on_close
        self.closed = False

    async def iter_lines(self) -> AsyncGenerator[str, None]:
        """逐行读取上游 SSE，并在收到取消信号后停止。"""
        async for line in self.response.aiter_lines():
            if self.cancel_event and self.cancel_event.is_set():
                break
            if line:
                yield f"{line}\n"

    async def aclose(self) -> None:
        """幂等关闭上游响应、HTTP 客户端与活动请求状态。"""
        if self.closed:
            return
        self.closed = True
        try:
            await self.response.aclose()
        finally:
            try:
                await self.http_client.aclose()
            finally:
                self.on_close()


class ClaudeClient:
    """负责向 Claude Messages 兼容服务发送请求。"""

    def __init__(
        self,
        api_key: Optional[str],
        base_url: str,
        anthropic_version: str,
        request_timeout: int = 90,
        read_timeout: int = 480,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.anthropic_version = anthropic_version
        self.timeout = httpx.Timeout(
            connect=request_timeout,
            read=read_timeout,
            write=request_timeout,
            pool=request_timeout,
        )
        self.transport = transport
        self.active_requests: Dict[str, asyncio.Event] = {}

    async def create_message(
        self, claude_request: Dict[str, Any], request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """发送非流式 Claude Messages 请求，支持取消。"""
        cancel_event = self._register_active_request(request_id)

        try:
            task = asyncio.create_task(self._post_message(claude_request, request_id))
            if cancel_event:
                cancel_task = asyncio.create_task(cancel_event.wait())
                done, pending = await asyncio.wait(
                    [task, cancel_task], return_when=asyncio.FIRST_COMPLETED
                )
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
                if cancel_task in done:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    raise HTTPException(status_code=499, detail="Request cancelled by client")

            return await task

        finally:
            self._remove_active_request(request_id)

    async def _post_message(
        self, claude_request: Dict[str, Any], request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """发送非流式请求的底层实现。"""
        async with self._create_http_client() as client:
            response = await client.post(
                self.build_messages_url(),
                headers=self.build_headers(request_id),
                json=claude_request,
            )
        return self.parse_json_response(response)

    async def create_message_stream(
        self, claude_request: Dict[str, Any], request_id: Optional[str] = None
    ) -> OpenedClaudeStream:
        """连接并校验流式 Claude Messages 请求，成功后返回已打开的流。"""
        cancel_event = self._register_active_request(request_id)
        http_client = self._create_http_client()
        response: Optional[httpx.Response] = None
        try:
            upstream_request = http_client.build_request(
                "POST",
                self.build_messages_url(),
                headers=self.build_headers(request_id),
                json=claude_request,
            )
            response = await http_client.send(upstream_request, stream=True)
            if response.status_code >= 400:
                await response.aread()
                self.raise_for_error_response(response)
            return OpenedClaudeStream(
                response,
                http_client,
                cancel_event,
                lambda: self._remove_active_request(request_id),
            )
        except httpx.TimeoutException as exc:
            await self._close_failed_stream(response, http_client, request_id)
            raise HTTPException(status_code=504, detail="连接上游服务超时。请稍后重试。") from exc
        except httpx.RequestError as exc:
            await self._close_failed_stream(response, http_client, request_id)
            raise HTTPException(status_code=502, detail="连接上游服务失败。请稍后重试。") from exc
        except Exception:
            await self._close_failed_stream(response, http_client, request_id)
            raise

    def cancel_request(self, request_id: str) -> bool:
        """取消一个活跃的请求。"""
        if request_id in self.active_requests:
            self.active_requests[request_id].set()
            return True
        return False

    def classify_claude_error(self, error_detail: str) -> str:
        """根据 Claude 错误内容提供分类和友好提示。"""
        error_lower = str(error_detail).lower()

        if "quota exhausted" in error_lower:
            return "上游 API Key 配额耗尽。请更换可用密钥或等待配额恢复。"

        if any(
            keyword in error_lower
            for keyword in [
                "invalid_api_key",
                "invalid x-api-key",
                "authentication_error",
                "unauthorized",
            ]
        ):
            return "API 密钥无效。请检查 CLAUDE_API_KEY 或 ANTHROPIC_API_KEY 配置是否正确。"

        if "rate_limit" in error_lower or "rate limit" in error_lower:
            return "请求频率超限。请稍后再试，或联系上游服务提供商提升配额。"

        if "model" in error_lower and ("not found" in error_lower or "does not exist" in error_lower):
            return "请求的模型不存在。请检查 model 参数是否正确。"

        if "invalid_request" in error_lower or "invalid request" in error_lower:
            return "请求参数错误。请检查 max_tokens、temperature 等参数是否在允许范围内。"

        if "permission" in error_lower or "forbidden" in error_lower:
            return "权限不足。请确认 API 密钥具有访问该模型或功能的权限。"

        if "overloaded" in error_lower or "over_capacity" in error_lower:
            return "上游服务过载。请稍后再试。"

        if any(
            keyword in error_lower
            for keyword in [
                "connection",
                "timeout",
                "network",
                "refused",
            ]
        ):
            return "连接到上游 Claude 服务失败。请检查网络连接和 CLAUDE_BASE_URL 配置。"

        return str(error_detail)

    def build_messages_url(self) -> str:
        """构造 Claude Messages 请求地址。"""
        return f"{self.base_url}/v1/messages"

    def build_headers(self, request_id: Optional[str] = None) -> Dict[str, str]:
        """构造上游请求头。"""
        headers = {
            "content-type": "application/json",
            "anthropic-version": self.anthropic_version,
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key
            headers["authorization"] = f"Bearer {self.api_key}"
        if request_id:
            headers["x-request-id"] = request_id
        return headers

    def parse_json_response(self, response: httpx.Response) -> Dict[str, Any]:
        """解析上游 JSON 响应。"""
        self.raise_for_error_response(response)
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail="Claude upstream returned invalid JSON") from exc

    def raise_for_error_response(self, response: httpx.Response) -> None:
        """解析并抛出上游 HTTP 错误。"""
        if response.status_code < 400:
            return
        error_message = self._extract_error_message(response)
        logger.warning(
            "claude_upstream_error status_code=%s detail=%s",
            response.status_code,
            sanitize_error_detail_for_log(error_message, self.api_key),
        )
        friendly_message = self.classify_claude_error(error_message)
        raise HTTPException(status_code=response.status_code, detail=friendly_message)

    def _create_http_client(self) -> httpx.AsyncClient:
        """创建使用统一超时和可选传输层的异步 HTTP 客户端。"""
        return httpx.AsyncClient(timeout=self.timeout, transport=self.transport)

    def _register_active_request(
        self, request_id: Optional[str]
    ) -> Optional[asyncio.Event]:
        """注册活动请求并返回对应取消事件。"""
        if not request_id:
            return None
        cancel_event = asyncio.Event()
        self.active_requests[request_id] = cancel_event
        return cancel_event

    def _remove_active_request(self, request_id: Optional[str]) -> None:
        """移除活动请求状态。"""
        if request_id:
            self.active_requests.pop(request_id, None)

    async def _close_failed_stream(
        self,
        response: Optional[httpx.Response],
        http_client: httpx.AsyncClient,
        request_id: Optional[str],
    ) -> None:
        """释放未成功返回给调用方的流式请求资源。"""
        try:
            if response is not None:
                await response.aclose()
        finally:
            try:
                await http_client.aclose()
            finally:
                self._remove_active_request(request_id)

    def _extract_error_message(self, response: httpx.Response) -> str:
        """从 Claude 错误响应中提取错误信息。"""
        try:
            data = response.json()
            if isinstance(data, dict):
                error_info = data.get("error", {})
                if isinstance(error_info, dict):
                    return error_info.get("message", response.text)
                return str(error_info)
            return response.text
        except json.JSONDecodeError:
            return response.text


def sanitize_error_detail_for_log(error_detail: str, api_key: Optional[str]) -> str:
    """脱敏并截断上游错误详情，避免日志泄露密钥或大量内容。"""
    sanitized_detail = str(error_detail)
    if api_key:
        sanitized_detail = sanitized_detail.replace(api_key, "[REDACTED]")
    return sanitized_detail[: Constants.MAX_ERROR_LOG_DETAIL_LENGTH]
