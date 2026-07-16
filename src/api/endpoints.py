"""OpenAI 兼容 API 路由。"""

import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.conversion.request_converter import convert_openai_to_claude_request
from src.conversion.response_converter import (
    convert_claude_response_to_openai,
    convert_claude_streaming_to_openai,
)
from src.core.client import ClaudeClient
from src.core.config import config
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
    logger.info(
        "chat_completion_received request_id=%s model=%s stream=%s message_count=%s tool_count=%s",
        request_id,
        request.model,
        bool(request.stream),
        len(request.messages),
        len(request.tools or []),
    )
    claude_request = convert_openai_to_claude_request(request)
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
        claude_stream = claude_client.create_message_stream(claude_request, request_id)
        logger.info("chat_completion_stream_started request_id=%s", request_id)
        return StreamingResponse(
            _stream_with_disconnect_check(claude_stream, request, http_request, request_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    claude_response = await claude_client.create_message(claude_request, request_id)
    logger.info("chat_completion_completed request_id=%s", request_id)
    return convert_claude_response_to_openai(claude_response, request)




async def _stream_with_disconnect_check(claude_stream, original_request, http_request, request_id):
    """流式响应包装器，检测客户端断开并取消上游请求。"""
    chunk_count = 0
    try:
        async for chunk in convert_claude_streaming_to_openai(
            claude_stream, original_request, request_id
        ):
            if await http_request.is_disconnected():
                logger.warning("chat_completion_client_disconnected request_id=%s", request_id)
                claude_client.cancel_request(request_id)
                break
            chunk_count += 1
            yield chunk
    finally:
        logger.info(
            "chat_completion_stream_finished request_id=%s chunk_count=%s",
            request_id,
            chunk_count,
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
