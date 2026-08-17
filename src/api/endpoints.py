"""OpenAI 兼容 API 路由。"""

import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.api.stream_errors import build_stream_error, format_stream_error_sse
from src.conversion.request_converter import convert_openai_to_claude_request
from src.conversion.response_converter import (
    convert_claude_response_to_openai,
    convert_claude_streaming_to_openai,
)
from src.core.client import ClaudeClient, OpenedClaudeStream
from src.core.config import config
from src.core.constants import Constants
from src.core.request_context import resolve_request_context
from src.core.zqi_catalog import ZqiCatalogClient, ZqiRouteResolver
from src.models.openai import OpenAIChatCompletionRequest

router = APIRouter()
logger = logging.getLogger(__name__)

claude_client = ClaudeClient(
    config.claude_api_key,
    config.claude_base_url,
    config.anthropic_version,
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


@router.post("/v1/chat/completions")
async def create_chat_completion(
    request: OpenAIChatCompletionRequest,
    http_request: Request,
    _: None = Depends(validate_api_key),
):
    """OpenAI Chat Completions 兼容入口。"""
    request_id = str(uuid.uuid4())
    request_context = resolve_request_context(http_request.headers)
    logger.info(
        "chat_completion_received request_id=%s model=%s stream=%s message_count=%s tool_count=%s",
        request_id,
        request.model,
        bool(request.stream),
        len(request.messages),
        len(request.tools or []),
    )
    claude_request = convert_openai_to_claude_request(request)
    route = await zqi_route_resolver.resolve(request.model)
    logger.info(
        "chat_completion_upstream_request request_id=%s model=%s stream=%s max_tokens=%s",
        request_id,
        claude_request["model"],
        claude_request["stream"],
        claude_request["max_tokens"],
    )

    if await http_request.is_disconnected():
        raise HTTPException(status_code=499, detail="Client disconnected")

    if request.stream:
        claude_request["stream"] = True
        opened_stream = await claude_client.create_message_stream(
            claude_request, request_id, request_context, route
        )
        logger.info("chat_completion_stream_started request_id=%s", request_id)
        return StreamingResponse(
            _stream_with_disconnect_check(opened_stream, request, http_request, request_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    claude_response = await claude_client.create_message(
        claude_request, request_id, request_context, route
    )
    logger.info("chat_completion_completed request_id=%s", request_id)
    return convert_claude_response_to_openai(claude_response, request)
async def _stream_with_disconnect_check(
    opened_stream: OpenedClaudeStream,
    original_request: OpenAIChatCompletionRequest,
    http_request: Request,
    request_id: str,
):
    """流式响应包装器，检测客户端断开并取消上游请求。"""
    chunk_count = 0
    error_code = None
    try:
        async for chunk in convert_claude_streaming_to_openai(
            opened_stream.iter_lines(), original_request, request_id
        ):
            if await http_request.is_disconnected():
                logger.warning("chat_completion_client_disconnected request_id=%s", request_id)
                claude_client.cancel_request(request_id)
                break
            chunk_count += 1
            yield chunk
    except Exception as exception:
        stream_error = build_stream_error(exception, request_id, claude_client.api_key)
        error_code = stream_error.code
        logger.warning(
            "chat_completion_stream_error request_id=%s code=%s exception_type=%s detail=%s",
            request_id,
            stream_error.code,
            type(exception).__name__,
            stream_error.log_detail,
        )
        chunk_count += 1
        yield format_stream_error_sse(stream_error)
        chunk_count += 1
        yield Constants.STREAM_DONE_EVENT
    finally:
        try:
            await opened_stream.aclose()
        except Exception as close_exception:
            logger.warning(
                "chat_completion_stream_close_error request_id=%s exception_type=%s",
                request_id,
                type(close_exception).__name__,
            )
        logger.info(
            "chat_completion_stream_finished request_id=%s chunk_count=%s error_code=%s",
            request_id,
            chunk_count,
            error_code,
        )


@router.api_route("/health", methods=["GET", "HEAD"])
async def health_check():
    """健康检查。"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "claude_base_url": config.claude_base_url,
        "claude_api_configured": bool(config.claude_api_key),
        "client_api_key_validation": bool(config.client_api_key),
    }


@router.api_route("/", methods=["GET", "HEAD"])
async def root():
    """根路径信息。"""
    return {
        "message": "OpenAI-to-Claude API Proxy v1.0.0",
        "endpoints": {"chat_completions": "/v1/chat/completions", "health": "/health"},
    }
