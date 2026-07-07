"""OpenAI Chat Completions 与 Claude Messages 转换测试。"""

import json
import asyncio

from src.conversion.request_converter import convert_openai_to_claude_request
from src.conversion.response_converter import (
    convert_claude_response_to_openai,
    convert_claude_streaming_to_openai,
)
from src.models.openai import OpenAIChatCompletionRequest


def test_convert_openai_request_to_claude_request_with_tools_and_tool_results():
    """Chat Completions 请求应转换为 Claude Messages 请求。"""
    request = OpenAIChatCompletionRequest(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "你是严格的助手。"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "识别这张图"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,iVBORw0KGgo="
                        },
                    },
                ],
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "lookup",
                            "arguments": '{"query":"天气"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "晴天"},
        ],
        max_tokens=256,
        temperature=0.2,
        stream=False,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "查询信息",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                },
            }
        ],
        tool_choice={"type": "function", "function": {"name": "lookup"}},
    )

    result = convert_openai_to_claude_request(request)

    assert result["model"] == "gpt-4o"
    assert result["system"] == "你是严格的助手。"
    assert result["max_tokens"] == 256
    assert result["temperature"] == 0.2
    assert result["tools"] == [
        {
            "name": "lookup",
            "description": "查询信息",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        }
    ]
    assert result["tool_choice"] == {"type": "tool", "name": "lookup"}
    assert result["messages"][0] == {
        "role": "user",
        "content": [
            {"type": "text", "text": "识别这张图"},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "iVBORw0KGgo=",
                },
            },
        ],
    }
    assert result["messages"][1]["content"] == [
        {
            "type": "tool_use",
            "id": "call_1",
            "name": "lookup",
            "input": {"query": "天气"},
        }
    ]
    assert result["messages"][2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "晴天"}],
    }


def test_convert_claude_response_to_openai_chat_completion_with_thinking_and_tool_use():
    """Claude Messages 响应应转换为 Chat Completions 响应。"""
    claude_response = {
        "id": "msg_123",
        "type": "message",
        "role": "assistant",
        "model": "claude-3-5-sonnet-20241022",
        "content": [
            {"type": "thinking", "thinking": "先分析"},
            {"type": "text", "text": "需要查询"},
            {
                "type": "tool_use",
                "id": "tool_1",
                "name": "lookup",
                "input": {"query": "天气"},
            },
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    request = OpenAIChatCompletionRequest(
        model="gpt-4o", messages=[{"role": "user", "content": "天气"}]
    )

    result = convert_claude_response_to_openai(claude_response, request)

    assert result["id"] == "msg_123"
    assert result["object"] == "chat.completion"
    assert result["model"] == "gpt-4o"
    assert result["choices"][0]["finish_reason"] == "tool_calls"
    message = result["choices"][0]["message"]
    assert message["role"] == "assistant"
    assert message["content"] == "需要查询"
    assert message["reasoning_content"] == "先分析"
    assert message["tool_calls"] == [
        {
            "id": "tool_1",
            "type": "function",
            "function": {"name": "lookup", "arguments": '{"query":"天气"}'},
        }
    ]
    assert result["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }


def test_convert_claude_streaming_to_openai_chat_completion_chunks():
    """Claude SSE 应转换为 Chat Completions SSE。"""

    async def run_stream_conversion():
        async def claude_stream():
            events = [
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_stream",
                        "model": "claude-3-5-sonnet-20241022",
                        "usage": {"input_tokens": 3, "output_tokens": 0},
                    },
                },
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "你好"},
                },
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 2},
                },
                {"type": "message_stop"},
            ]
            for event in events:
                yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

        request = OpenAIChatCompletionRequest(
            model="gpt-4o", messages=[{"role": "user", "content": "你好"}], stream=True
        )

        return [
            chunk async for chunk in convert_claude_streaming_to_openai(claude_stream(), request)
        ]

    chunks = asyncio.run(run_stream_conversion())

    assert chunks[-1] == "data: [DONE]\n\n"
    payloads = [
        json.loads(chunk.removeprefix("data: "))
        for chunk in chunks
        if chunk.startswith("data: {")
    ]
    assert payloads[0]["object"] == "chat.completion.chunk"
    assert payloads[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert payloads[1]["choices"][0]["delta"] == {"content": "你好"}
    assert payloads[-1]["choices"][0]["finish_reason"] == "stop"
    assert payloads[-1]["usage"] == {
        "prompt_tokens": 3,
        "completion_tokens": 2,
        "total_tokens": 5,
    }
