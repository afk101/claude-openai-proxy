"""Claude 上游客户端测试。"""

import asyncio
import logging

import httpx
import pytest
from fastapi import HTTPException

from src.core.client import ClaudeClient


class TrackingAsyncByteStream(httpx.AsyncByteStream):
    """记录异步响应流的关闭次数。"""

    def __init__(self, chunks):
        self.chunks = chunks
        self.close_count = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.close_count += 1


def test_parse_json_response_logs_upstream_error_details(caplog):
    """上游返回错误时应记录脱敏错误详情，便于定位协议问题。"""
    client = ClaudeClient(None, "https://example.com", "2023-06-01")
    response = httpx.Response(
        400,
        json={"error": {"message": "image blocks are not supported"}},
        request=httpx.Request("POST", "https://example.com/v1/messages"),
    )
    caplog.set_level(logging.WARNING)

    with pytest.raises(HTTPException):
        client.parse_json_response(response)

    assert any("claude_upstream_error" in record.message for record in caplog.records)
    assert any("image blocks are not supported" in record.message for record in caplog.records)


def test_create_message_stream_rejects_upstream_error_before_returning_stream():
    """上游错误状态应在创建本地 StreamingResponse 前抛出。"""

    async def run_test():
        async def handler(request):
            return httpx.Response(
                408,
                json={"error": {"message": "timeout awaiting response headers"}},
            )

        client = ClaudeClient(
            "secret-key",
            "https://example.com",
            "2023-06-01",
            transport=httpx.MockTransport(handler),
        )

        with pytest.raises(HTTPException) as error:
            await client.create_message_stream(
                {"model": "test", "messages": [], "stream": True},
                "req-408",
            )

        assert error.value.status_code == 408
        assert "req-408" not in client.active_requests

    asyncio.run(run_test())


def test_opened_message_stream_reads_lines_and_closes_once():
    """已打开流应读取 SSE，并在重复关闭时只释放一次底层响应。"""

    async def run_test():
        response_stream = TrackingAsyncByteStream(
            [b'data: {"type":"message_start"}\n\n']
        )

        async def handler(request):
            return httpx.Response(200, stream=response_stream)

        client = ClaudeClient(
            None,
            "https://example.com",
            "2023-06-01",
            transport=httpx.MockTransport(handler),
        )
        opened_stream = await client.create_message_stream(
            {"model": "test", "messages": [], "stream": True},
            "req-success",
        )

        lines = [line async for line in opened_stream.iter_lines()]
        await opened_stream.aclose()
        await opened_stream.aclose()

        assert lines == ['data: {"type":"message_start"}\n']
        assert response_stream.close_count == 1
        assert "req-success" not in client.active_requests

    asyncio.run(run_test())


def test_classify_claude_error_identifies_exhausted_quota():
    """额度耗尽应与密钥无效区分。"""
    client = ClaudeClient(None, "https://example.com", "2023-06-01")

    assert client.classify_claude_error(
        "claudecodecompat: api key quota exhausted"
    ) == "上游 API Key 配额耗尽。请更换可用密钥或等待配额恢复。"
