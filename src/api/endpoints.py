"""OpenAI 兼容 API 路由。"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from src.core.client import (
    OpenedResponsesStream,
    ResponsesUpstreamClient,
)
from src.core.config import config
from src.core.constants import Constants
from src.core.request_context import resolve_request_context
from src.core.zqi_catalog import ZqiCatalogClient, ZqiRouteResolver
from src.models.responses import parse_responses_envelope

router = APIRouter()
logger = logging.getLogger(__name__)

responses_client = ResponsesUpstreamClient(
    config.upstream_api_key,
    config.upstream_base_url,
    config.request_timeout,
    config.read_timeout,
)
zqi_route_resolver = ZqiRouteResolver(ZqiCatalogClient())


async def validate_api_key(
    x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)
) -> None:
    """校验代理客户端 API Key。"""
    client_api_key = extract_client_api_key(x_api_key, authorization)
    if not config.validate_client_api_key(client_api_key or ""):
        raise HTTPException(status_code=401, detail="Invalid API key")


def extract_client_api_key(x_api_key: Optional[str], authorization: Optional[str]) -> Optional[str]:
    """从请求头提取客户端 API Key。"""
    if x_api_key:
        return x_api_key
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ")
    return None


@router.post(Constants.RESPONSES_CREATE_PATH)
async def create_response(
    http_request: Request,
    _: None = Depends(validate_api_key),
) -> Response:
    """编排 Responses 原字节请求，不解释或重写业务内容。"""
    raw_body = await http_request.body()
    envelope = parse_responses_envelope(raw_body)

    # Base URL 必须在目录访问前验证，避免本地配置错误触发无意义的外部请求。
    responses_client.build_responses_url()
    request_id = str(uuid.uuid4())
    request_context = resolve_request_context(http_request.headers)
    accept_encoding = _resolve_accept_encoding(http_request)
    logger.info(
        "responses_received request_id=%s model=%s stream=%s",
        request_id,
        envelope.model,
        envelope.stream,
    )
    route = await zqi_route_resolver.resolve(envelope.model)
    route_type = (
        Constants.RESPONSES_ROUTE_TYPE_PACKAGE
        if route and route.api_key
        else Constants.RESPONSES_ROUTE_TYPE_ORDINARY
    )
    if envelope.stream:
        opened_stream = await responses_client.open_stream_response(
            raw_body,
            request_id,
            request_context,
            route,
            accept_encoding,
        )
        logger.info(
            "responses_stream_started request_id=%s model=%s route_type=%s status=%s",
            request_id,
            envelope.model,
            route_type,
            opened_stream.status_code,
        )
        response = _ManagedResponsesStreamingResponse(
            opened_stream,
            request_id,
            envelope.model,
            route_type,
        )
        # mapping 形式会折叠重复 Header；流式响应同样必须直接交付过滤后的 raw_headers。
        response.raw_headers = list(opened_stream.raw_headers)
        return response

    upstream_response = await responses_client.create_non_stream_response(
        raw_body,
        request_id,
        request_context,
        route,
        accept_encoding,
    )
    logger.info(
        "responses_completed request_id=%s model=%s route_type=%s status=%s bytes=%s",
        request_id,
        envelope.model,
        route_type,
        upstream_response.status_code,
        len(upstream_response.body),
    )

    response = Response(
        content=upstream_response.body,
        status_code=upstream_response.status_code,
    )
    # Starlette 的 mapping Header 会折叠同名字段；直接赋 raw_headers 才能保留重复项。
    response.raw_headers = list(upstream_response.raw_headers)
    return response


class _ManagedResponsesStreamingResponse(StreamingResponse):
    """把 Starlette 的断连监听与 Responses 上游资源的幂等关闭绑在一起。"""

    def __init__(
        self,
        opened_stream: OpenedResponsesStream,
        request_id: str,
        model: str,
        route_type: str,
    ) -> None:
        self.opened_stream = opened_stream
        self.request_id = request_id
        self.model = model
        self.route_type = route_type
        super().__init__(
            _iterate_responses_stream(opened_stream, request_id),
            status_code=opened_stream.status_code,
        )

    async def __call__(self, scope, receive, send) -> None:
        """无论正常完成、断连、发送失败还是任务取消，都在退出 ASGI 调用前清理。"""
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.opened_stream.aclose()
            logger.info(
                "responses_stream_finished request_id=%s model=%s route_type=%s",
                self.request_id,
                self.model,
                self.route_type,
            )


async def _iterate_responses_stream(
    opened_stream: OpenedResponsesStream,
    request_id: str,
):
    """直接产出上游 raw bytes；流开始后的异常只终止 body，不生成协议事件。"""
    try:
        async for chunk in opened_stream.iter_raw():
            yield chunk
    except asyncio.CancelledError:
        # 断连或 ASGI task cancel 必须继续向上游 raw iterator 传播取消。
        raise
    except Exception as exception:
        # 不记录 exception 文本，避免远端错误把响应内容或凭据带入日志。
        logger.warning(
            "responses_stream_interrupted request_id=%s exception_type=%s",
            request_id,
            type(exception).__name__,
        )


def _resolve_accept_encoding(http_request: Request) -> str:
    """按 HTTP 列表语义合并重复 Accept-Encoding，空值时明确请求 identity。"""
    values = [
        value
        for value in http_request.headers.getlist(Constants.HEADER_ACCEPT_ENCODING)
        if value.strip()
    ]
    if not values:
        return Constants.RESPONSES_DEFAULT_ACCEPT_ENCODING
    return ", ".join(values)


@router.get(Constants.HEALTH_PATH)
@router.head(Constants.HEALTH_PATH, include_in_schema=False)
async def health_check():
    """健康检查。"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "upstream_base_url_configured": bool(config.upstream_base_url),
        "ordinary_api_key_configured": bool(config.upstream_api_key),
        "client_api_key_validation": bool(config.client_api_key),
    }


@router.get(Constants.ROOT_PATH)
@router.head(Constants.ROOT_PATH, include_in_schema=False)
async def root():
    """根路径信息。"""
    return {
        "message": f"{Constants.APP_NAME} v{Constants.APP_VERSION}",
        "endpoints": {
            "responses": Constants.RESPONSES_CREATE_PATH,
            "health": Constants.HEALTH_PATH,
        },
    }
