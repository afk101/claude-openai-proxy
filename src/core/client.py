"""Claude Messages 兼容上游客户端。"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable, Dict, NoReturn, Optional, Sequence, Set, Tuple
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import HTTPException

from src.core.constants import Constants
from src.core.request_context import UpstreamRequestContext
from src.core.zqi_catalog import ZqiRoute

logger = logging.getLogger(__name__)


def _raise_invalid_responses_base_url() -> NoReturn:
    """统一抛出不包含原始配置值的 Base URL 错误，防止敏感 query 被回显。"""
    raise HTTPException(
        status_code=500,
        detail=Constants.RESPONSES_INVALID_BASE_URL_DETAIL,
    )


@dataclass(frozen=True)
class BufferedUpstreamResponse:
    """已完整读取、可以安全交给下游的 Responses 上游响应。"""

    status_code: int
    body: bytes
    raw_headers: Tuple[Tuple[bytes, bytes], ...]


class ResponsesUpstreamClient:
    """封装 Responses 原字节转发与资源生命周期。"""

    def __init__(
        self,
        api_key: Optional[str],
        base_url: Optional[str],
        request_timeout: int = 90,
        read_timeout: int = 480,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = httpx.Timeout(
            connect=request_timeout,
            read=read_timeout,
            write=request_timeout,
            pool=request_timeout,
        )
        self.transport = transport
        self.active_requests: Set[str] = set()

    def build_responses_url(self) -> str:
        """校验服务根地址并且只追加一次 Responses Create 路径。

        配置错误不回显原值，避免 URL 中意外携带的敏感信息进入响应或日志。
        """
        base_url = self.base_url
        if (
            not isinstance(base_url, str)
            or not base_url
            or base_url != base_url.strip()
            or any(character.isspace() for character in base_url)
        ):
            _raise_invalid_responses_base_url()

        # urllib 会对缺失右方括号的 IPv6 和非法端口抛 ValueError，必须归一为脱敏配置错误。
        try:
            parsed = urlsplit(base_url)
            parsed.port
        except ValueError:
            _raise_invalid_responses_base_url()
        normalized_path = parsed.path.rstrip("/")
        invalid_suffix = normalized_path.endswith("/v1") or normalized_path.endswith(
            Constants.RESPONSES_CREATE_PATH
        )
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.hostname is None
            or "?" in base_url
            or "#" in base_url
            or parsed.query
            or parsed.fragment
            or invalid_suffix
        ):
            _raise_invalid_responses_base_url()

        normalized_base = urlunsplit(
            (parsed.scheme, parsed.netloc, normalized_path, "", "")
        )
        return f"{normalized_base}{Constants.RESPONSES_CREATE_PATH}"

    async def create_non_stream_response(
        self,
        raw_body: bytes,
        request_id: str,
        request_context: UpstreamRequestContext,
        route: Optional[ZqiRoute],
        accept_encoding: str,
    ) -> BufferedUpstreamResponse:
        """发送非流式请求并在下游开始前完整聚合上游原始字节。

        手动 streaming 模式配合 aiter_raw 可避免 HTTPX 解压响应后仍保留
        Content-Encoding。无论连接、读取或取消发生在哪一阶段，finally 都会
        幂等关闭 response/client 并移除活动请求记录。
        """
        target_url = self.build_responses_url()
        headers = self.build_headers(request_id, request_context, route, accept_encoding)
        http_client = self._create_http_client()
        response: Optional[httpx.Response] = None
        self.active_requests.add(request_id)
        try:
            upstream_request = http_client.build_request(
                Constants.RESPONSES_HTTP_METHOD,
                target_url,
                headers=headers,
                content=raw_body,
            )
            response = await http_client.send(upstream_request, stream=True)
            raw_headers = self.filter_response_headers(response.headers.raw)
            chunks = [chunk async for chunk in response.aiter_raw()]
            return BufferedUpstreamResponse(
                status_code=response.status_code,
                body=b"".join(chunks),
                raw_headers=raw_headers,
            )
        except httpx.TimeoutException as exc:
            raise HTTPException(
                status_code=504,
                detail=Constants.RESPONSES_UPSTREAM_TIMEOUT_DETAIL,
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail=Constants.RESPONSES_UPSTREAM_CONNECTION_DETAIL,
            ) from exc
        finally:
            try:
                await self._close_upstream_resources(response, http_client)
            finally:
                # 即使任务取消打断清理 await，活动记录也必须从进程内状态移除。
                self.active_requests.discard(request_id)

    async def open_stream_response(
        self,
        raw_body: bytes,
        request_id: str,
        request_context: UpstreamRequestContext,
        route: Optional[ZqiRoute],
        accept_encoding: str,
    ) -> "OpenedResponsesStream":
        """建立 Responses 流并把后续读取与关闭责任移交给资源句柄。

        该方法只负责下游响应开始前的连接阶段。连接超时和网络错误仍可安全
        映射为 504/502；一旦取得上游响应，status、Header 和 raw body 的所有权
        都交给 ``OpenedResponsesStream``，避免 endpoint 与 HTTP 客户端重复关闭。
        """
        target_url = self.build_responses_url()
        headers = self.build_headers(request_id, request_context, route, accept_encoding)
        http_client = self._create_http_client()
        response: Optional[httpx.Response] = None
        self.active_requests.add(request_id)
        try:
            upstream_request = http_client.build_request(
                Constants.RESPONSES_HTTP_METHOD,
                target_url,
                headers=headers,
                content=raw_body,
            )
            response = await http_client.send(upstream_request, stream=True)
            return OpenedResponsesStream(
                response=response,
                http_client=http_client,
                raw_headers=self.filter_response_headers(response.headers.raw),
                on_close=lambda: self.active_requests.discard(request_id),
            )
        except httpx.TimeoutException as exc:
            try:
                await self._close_upstream_resources(response, http_client)
            finally:
                self.active_requests.discard(request_id)
            raise HTTPException(
                status_code=504,
                detail=Constants.RESPONSES_UPSTREAM_TIMEOUT_DETAIL,
            ) from exc
        except httpx.RequestError as exc:
            try:
                await self._close_upstream_resources(response, http_client)
            finally:
                self.active_requests.discard(request_id)
            raise HTTPException(
                status_code=502,
                detail=Constants.RESPONSES_UPSTREAM_CONNECTION_DETAIL,
            ) from exc
        except BaseException:
            # CancelledError 不属于普通网络错误，必须原样传播；传播前仍释放已创建资源。
            try:
                await self._close_upstream_resources(response, http_client)
            finally:
                self.active_requests.discard(request_id)
            raise

    def build_headers(
        self,
        request_id: str,
        request_context: UpstreamRequestContext,
        route: Optional[ZqiRoute],
        accept_encoding: str,
    ) -> Dict[str, str]:
        """从代理控制的数据构造允许集合，拒绝入站凭据和无关 Header 渗透。"""
        api_key = route.api_key if route and route.api_key else self.api_key
        if not api_key:
            raise HTTPException(
                status_code=500,
                detail=Constants.RESPONSES_MISSING_API_KEY_DETAIL,
            )
        headers = {
            Constants.HEADER_CONTENT_TYPE: Constants.RESPONSES_CONTENT_TYPE,
            Constants.HEADER_AUTHORIZATION: f"Bearer {api_key}",
            Constants.HEADER_API_KEY: api_key,
            Constants.HEADER_CLIENT_SOURCE: Constants.CLIENT_SOURCE_IDE,
            Constants.HEADER_REQUEST_ID: request_id,
            Constants.HEADER_CLIENT_TASK_ID: request_context.client_task_id,
            Constants.HEADER_CLIENT_TRACE_ID: request_context.client_trace_id,
            Constants.HEADER_ACCEPT_ENCODING: accept_encoding,
        }
        if route:
            headers.update(route.headers)
        return headers

    @staticmethod
    def filter_response_headers(
        raw_headers: Sequence[Tuple[bytes, bytes]],
    ) -> Tuple[Tuple[bytes, bytes], ...]:
        """保留端到端及重复 Header，并移除静态和 Connection 动态逐跳字段。"""
        filtered_names = {name.encode("ascii") for name in Constants.RESPONSE_FILTERED_HEADERS}
        for name, value in raw_headers:
            if name.lower() != b"connection":
                continue
            # 一个响应可出现多个 Connection 字段，每个字段又可声明多个逐跳名称。
            filtered_names.update(
                token.strip().lower() for token in value.split(b",") if token.strip()
            )
        return tuple(
            (name, value)
            for name, value in raw_headers
            if name.lower() not in filtered_names
        )

    def _create_http_client(self) -> httpx.AsyncClient:
        """创建带统一超时和可替换远端 transport 的异步客户端。"""
        return httpx.AsyncClient(timeout=self.timeout, transport=self.transport)

    @staticmethod
    async def _close_upstream_resources(
        response: Optional[httpx.Response], http_client: httpx.AsyncClient
    ) -> None:
        """依次关闭上游响应与客户端，不让普通清理错误覆盖业务结果。"""
        try:
            if response is not None:
                await response.aclose()
        except Exception as exc:
            logger.warning(
                "responses_response_close_error exception_type=%s",
                type(exc).__name__,
            )
        try:
            await http_client.aclose()
        except Exception as exc:
            logger.warning(
                "responses_client_close_error exception_type=%s",
                type(exc).__name__,
            )


class OpenedResponsesStream:
    """持有已建立的 Responses raw stream，并提供幂等资源关闭边界。"""

    def __init__(
        self,
        response: httpx.Response,
        http_client: httpx.AsyncClient,
        raw_headers: Tuple[Tuple[bytes, bytes], ...],
        on_close: Callable[[], None],
    ) -> None:
        self.response = response
        self.http_client = http_client
        self.status_code = response.status_code
        self.raw_headers = raw_headers
        self.on_close = on_close
        self._closed = False
        self._close_lock = asyncio.Lock()

    async def iter_raw(self) -> AsyncGenerator[bytes, None]:
        """逐块读取未解压字节，不解释 SSE 行、事件或终止标志。"""
        async for chunk in self.response.aiter_raw():
            yield chunk

    async def aclose(self) -> None:
        """最多一次关闭 response/client，并始终清除活动请求记录。

        锁覆盖完整关闭过程，使断连监听、下游发送失败和任务取消即使同时到达，
        也不会重复关闭同一资源。普通 close 异常只写入脱敏类型；取消异常在完成
        其余清理后继续传播，不能被资源回收逻辑吞掉。
        """
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            cancellation: Optional[asyncio.CancelledError] = None
            try:
                await self.response.aclose()
            except asyncio.CancelledError as exc:
                cancellation = exc
            except Exception as exc:
                logger.warning(
                    "responses_response_close_error exception_type=%s",
                    type(exc).__name__,
                )
            try:
                await self.http_client.aclose()
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
            except Exception as exc:
                logger.warning(
                    "responses_client_close_error exception_type=%s",
                    type(exc).__name__,
                )
            finally:
                self.on_close()
            if cancellation is not None:
                raise cancellation


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
        self,
        claude_request: Dict[str, Any],
        request_id: Optional[str] = None,
        request_context: Optional[UpstreamRequestContext] = None,
        route: Optional[ZqiRoute] = None,
    ) -> Dict[str, Any]:
        """发送非流式 Claude Messages 请求，支持取消。"""
        cancel_event = self._register_active_request(request_id)

        try:
            task = asyncio.create_task(
                self._post_message(claude_request, request_id, request_context, route)
            )
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
        self,
        claude_request: Dict[str, Any],
        request_id: Optional[str] = None,
        request_context: Optional[UpstreamRequestContext] = None,
        route: Optional[ZqiRoute] = None,
    ) -> Dict[str, Any]:
        """发送非流式请求的底层实现。"""
        async with self._create_http_client() as client:
            response = await client.post(
                self.build_messages_url(),
                headers=self.build_headers(request_id, request_context, route),
                json=claude_request,
            )
        return self.parse_json_response(response, route.api_key if route else None)

    async def create_message_stream(
        self,
        claude_request: Dict[str, Any],
        request_id: Optional[str] = None,
        request_context: Optional[UpstreamRequestContext] = None,
        route: Optional[ZqiRoute] = None,
    ) -> OpenedClaudeStream:
        """连接并校验流式 Claude Messages 请求，成功后返回已打开的流。"""
        cancel_event = self._register_active_request(request_id)
        http_client = self._create_http_client()
        response: Optional[httpx.Response] = None
        try:
            upstream_request = http_client.build_request(
                "POST",
                self.build_messages_url(),
                headers=self.build_headers(request_id, request_context, route),
                json=claude_request,
            )
            response = await http_client.send(upstream_request, stream=True)
            if response.status_code >= 400:
                await response.aread()
                self.raise_for_error_response(response, route.api_key if route else None)
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

    def build_headers(
        self,
        request_id: Optional[str] = None,
        request_context: Optional[UpstreamRequestContext] = None,
        route: Optional[ZqiRoute] = None,
    ) -> Dict[str, str]:
        """构造上游请求头。"""
        headers = {
            "content-type": "application/json",
            "anthropic-version": self.anthropic_version,
            Constants.HEADER_CLIENT_SOURCE: Constants.CLIENT_SOURCE_IDE,
        }
        api_key = route.api_key if route and route.api_key else self.api_key
        if api_key:
            headers["x-api-key"] = api_key
            headers["authorization"] = f"Bearer {api_key}"
        if route:
            headers.update(route.headers)
        if request_id:
            headers["x-request-id"] = request_id
        if request_context:
            headers[Constants.HEADER_CLIENT_TASK_ID] = request_context.client_task_id
            headers[Constants.HEADER_CLIENT_TRACE_ID] = request_context.client_trace_id
        return headers

    def parse_json_response(
        self, response: httpx.Response, route_api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """解析上游 JSON 响应。"""
        self.raise_for_error_response(response, route_api_key)
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail="Claude upstream returned invalid JSON") from exc

    def raise_for_error_response(
        self, response: httpx.Response, route_api_key: Optional[str] = None
    ) -> None:
        """解析并抛出上游 HTTP 错误。"""
        if response.status_code < 400:
            return
        error_message = self._extract_error_message(response)
        logger.warning(
            "claude_upstream_error status_code=%s detail=%s",
            response.status_code,
            sanitize_error_detail_for_log(
                error_message, route_api_key or self.api_key
            ),
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
