"""Claude 上游客户端测试。"""

import logging

import httpx
import pytest
from fastapi import HTTPException

from src.core.client import ClaudeClient


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
