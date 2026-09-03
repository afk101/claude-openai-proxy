"""OpenAI Responses 透明上游客户端。"""

import asyncio
import logging
from dataclasses import dataclass
from typing import AsyncGenerator, Callable, Dict, NoReturn, Optional, Sequence, Set, Tuple
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


async def _close_httpx_resources(
    response: Optional[httpx.Response],
    http_client: httpx.AsyncClient,
) -> None:
    """依次关闭 response/client，并在完整清理后继续传播首次取消。

    普通关闭异常只记录类型，不能覆盖已经取得的业务响应；CancelledError
    则必须在尝试关闭两个资源后恢复，保证任务取消语义和资源回收同时成立。
    """
    cancellation: Optional[asyncio.CancelledError] = None
    try:
        if response is not None:
            await response.aclose()
    except asyncio.CancelledError as exc:
        cancellation = exc
    except Exception as exc:
        logger.warning(
            "responses_response_close_error exception_type=%s",
            type(exc).__name__,
        )
    try:
        await http_client.aclose()
    except asyncio.CancelledError as exc:
        cancellation = cancellation or exc
    except Exception as exc:
        logger.warning(
            "responses_client_close_error exception_type=%s",
            type(exc).__name__,
        )
    if cancellation is not None:
        raise cancellation


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
        response, http_client = await self._open_upstream_response(
            raw_body,
            request_id,
            request_context,
            route,
            accept_encoding,
        )
        try:
            raw_headers = self.filter_response_headers(response.headers.raw)
            chunks = [chunk async for chunk in response.aiter_raw()]
            return BufferedUpstreamResponse(
                status_code=response.status_code,
                body=b"".join(chunks),
                raw_headers=raw_headers,
            )
        except httpx.RequestError as exc:
            self._raise_upstream_request_error(exc)
        finally:
            await self._close_request(response, http_client, request_id)

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
        response, http_client = await self._open_upstream_response(
            raw_body,
            request_id,
            request_context,
            route,
            accept_encoding,
        )
        return OpenedResponsesStream(
            response=response,
            http_client=http_client,
            raw_headers=self.filter_response_headers(response.headers.raw),
            on_close=lambda: self.active_requests.discard(request_id),
        )

    async def _open_upstream_response(
        self,
        raw_body: bytes,
        request_id: str,
        request_context: UpstreamRequestContext,
        route: Optional[ZqiRoute],
        accept_encoding: str,
    ) -> Tuple[httpx.Response, httpx.AsyncClient]:
        """统一建立 raw 上游响应，并在连接失败或取消时回收全部资源。

        成功返回后，调用方接管 response/client 和活动记录；失败路径在这里完成
        清理，避免流式与非流式连接阶段分别维护错误映射和生命周期逻辑。
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
            return response, http_client
        except httpx.RequestError as exc:
            await self._close_request(response, http_client, request_id)
            self._raise_upstream_request_error(exc)
        except BaseException:
            # CancelledError 不属于普通网络错误，必须原样传播；传播前仍释放已创建资源。
            await self._close_request(response, http_client, request_id)
            raise

    @staticmethod
    def _raise_upstream_request_error(exception: httpx.RequestError) -> NoReturn:
        """把连接/读取异常稳定映射为 502 或 504，且不暴露远端异常文本。"""
        if isinstance(exception, httpx.TimeoutException):
            raise HTTPException(
                status_code=504,
                detail=Constants.RESPONSES_UPSTREAM_TIMEOUT_DETAIL,
            ) from exception
        raise HTTPException(
            status_code=502,
            detail=Constants.RESPONSES_UPSTREAM_CONNECTION_DETAIL,
        ) from exception

    async def _close_request(
        self,
        response: Optional[httpx.Response],
        http_client: httpx.AsyncClient,
        request_id: str,
    ) -> None:
        """关闭单次请求资源，并在清理被取消时也移除活动请求记录。"""
        try:
            await _close_httpx_resources(response, http_client)
        finally:
            self.active_requests.discard(request_id)

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
            try:
                await _close_httpx_resources(self.response, self.http_client)
            finally:
                self.on_close()
