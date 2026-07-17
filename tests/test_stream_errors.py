"""流式错误契约测试。"""

import json

import httpx

from src.api.stream_errors import build_stream_error, format_stream_error_sse


def parse_sse_payload(event: str):
    """解析单条 SSE data 事件。"""
    return json.loads(event.removeprefix("data: ").strip())


def test_timeout_stream_error_has_stable_openai_shape():
    """流读取超时应转换为稳定且脱敏的 OpenAI 错误结构。"""
    stream_error = build_stream_error(
        httpx.ReadTimeout("timed out with secret-key"),
        "req-timeout",
        "secret-key",
    )

    assert parse_sse_payload(format_stream_error_sse(stream_error)) == {
        "error": {
            "message": "上游流式响应超时，请稍后重试。",
            "type": "upstream_stream_error",
            "code": "upstream_timeout",
            "request_id": "req-timeout",
        }
    }
    assert stream_error.log_detail == "timed out with [REDACTED]"


def test_connection_stream_error_has_stable_code():
    """流连接错误应与超时和转换错误区分。"""
    exception = httpx.ConnectError(
        "connection reset",
        request=httpx.Request("POST", "https://example.com/v1/messages"),
    )

    stream_error = build_stream_error(exception, "req-connect", None)

    assert stream_error.code == "upstream_connection_error"
    assert stream_error.message == "上游流式连接中断，请稍后重试。"


def test_conversion_stream_error_does_not_expose_internal_exception():
    """未知转换错误应返回通用提示，并仅在日志字段保留脱敏摘要。"""
    stream_error = build_stream_error(
        ValueError("invalid payload with secret-key"),
        "req-convert",
        "secret-key",
    )
    event = format_stream_error_sse(stream_error)

    assert stream_error.code == "stream_conversion_error"
    assert stream_error.message == "流式响应转换失败，请稍后重试。"
    assert stream_error.log_detail == "invalid payload with [REDACTED]"
    assert "secret-key" not in event
    assert "invalid payload" not in event
