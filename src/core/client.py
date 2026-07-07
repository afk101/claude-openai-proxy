"""Claude Messages 兼容上游客户端。"""

import asyncio
import json
from typing import Any, AsyncGenerator, Dict, Optional

import httpx
from fastapi import HTTPException


class ClaudeClient:
    """负责向 Claude Messages 兼容服务发送请求。"""

    def __init__(
        self,
        api_key: Optional[str],
        base_url: str,
        anthropic_version: str,
        request_timeout: int = 90,
        read_timeout: int = 480,
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
        self.active_requests: Dict[str, asyncio.Event] = {}

    async def create_message(
        self, claude_request: Dict[str, Any], request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """发送非流式 Claude Messages 请求，支持取消。"""
        cancel_event: Optional[asyncio.Event] = None
        if request_id:
            cancel_event = asyncio.Event()
            self.active_requests[request_id] = cancel_event

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
            if request_id and request_id in self.active_requests:
                del self.active_requests[request_id]

    async def _post_message(
        self, claude_request: Dict[str, Any], request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """发送非流式请求的底层实现。"""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.build_messages_url(),
                headers=self.build_headers(request_id),
                json=claude_request,
            )
        return self.parse_json_response(response)

    async def create_message_stream(
        self, claude_request: Dict[str, Any], request_id: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """发送流式 Claude Messages 请求，并透传 SSE 行，支持取消。"""
        cancel_event: Optional[asyncio.Event] = None
        if request_id:
            cancel_event = asyncio.Event()
            self.active_requests[request_id] = cancel_event

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST",
                    self.build_messages_url(),
                    headers=self.build_headers(request_id),
                    json=claude_request,
                ) as response:
                    if response.status_code >= 400:
                        detail = await response.aread()
                        raise HTTPException(status_code=response.status_code, detail=detail.decode("utf-8"))
                    async for line in response.aiter_lines():
                        if request_id and request_id in self.active_requests:
                            if self.active_requests[request_id].is_set():
                                break
                        if line:
                            yield f"{line}\n"
        finally:
            if request_id and request_id in self.active_requests:
                del self.active_requests[request_id]

    def cancel_request(self, request_id: str) -> bool:
        """取消一个活跃的请求。"""
        if request_id in self.active_requests:
            self.active_requests[request_id].set()
            return True
        return False

    def classify_claude_error(self, error_detail: str) -> str:
        """根据 Claude 错误内容提供分类和友好提示。"""
        error_lower = str(error_detail).lower()

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
        if response.status_code >= 400:
            error_message = self._extract_error_message(response)
            friendly_message = self.classify_claude_error(error_message)
            raise HTTPException(status_code=response.status_code, detail=friendly_message)
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail="Claude upstream returned invalid JSON") from exc

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
