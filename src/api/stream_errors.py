"""OpenAI 兼容流式错误分类与序列化。"""

import json
from dataclasses import dataclass
from typing import Optional

import httpx

from src.core.client import sanitize_error_detail_for_log
from src.core.constants import Constants


@dataclass(frozen=True)
class StreamError:
    """表示可安全返回和记录的流式错误。"""

    message: str
    error_type: str
    code: str
    request_id: str
    log_detail: str


def build_stream_error(
    exception: Exception,
    request_id: str,
    api_key: Optional[str],
) -> StreamError:
    """将流中异常分类为稳定且脱敏的错误对象。"""
    message, code = classify_stream_error(exception)
    return StreamError(
        message=message,
        error_type=Constants.STREAM_ERROR_TYPE,
        code=code,
        request_id=request_id,
        log_detail=sanitize_error_detail_for_log(str(exception), api_key),
    )


def classify_stream_error(exception: Exception) -> tuple[str, str]:
    """根据异常类型返回对外消息与稳定错误码。"""
    if isinstance(exception, httpx.TimeoutException):
        return (
            Constants.STREAM_ERROR_TIMEOUT_MESSAGE,
            Constants.STREAM_ERROR_TIMEOUT_CODE,
        )
    if isinstance(exception, httpx.RequestError):
        return (
            Constants.STREAM_ERROR_CONNECTION_MESSAGE,
            Constants.STREAM_ERROR_CONNECTION_CODE,
        )
    return (
        Constants.STREAM_ERROR_CONVERSION_MESSAGE,
        Constants.STREAM_ERROR_CONVERSION_CODE,
    )


def format_stream_error_sse(stream_error: StreamError) -> str:
    """把流式错误格式化为 OpenAI SSE data 事件。"""
    payload = {
        "error": {
            "message": stream_error.message,
            "type": stream_error.error_type,
            "code": stream_error.code,
            "request_id": stream_error.request_id,
        }
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
