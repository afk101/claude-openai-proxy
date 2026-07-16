"""OpenAI Chat Completions 与 Claude Messages 转换测试。"""

import json
import asyncio
import logging

from src.conversion.request_converter import convert_openai_to_claude_request, resolve_max_tokens
from src.conversion.response_converter import (
    convert_claude_response_to_openai,
    convert_claude_streaming_to_openai,
)
from src.models.openai import OpenAIChatCompletionRequest


def test_resolve_max_tokens_uses_client_limit_for_arbitrary_model():
    """任意模型应使用调用方指定的输出 token 上限。"""
    request = OpenAIChatCompletionRequest(
        model="claude-4.8-opus", messages=[], max_tokens=123456
    )

    assert resolve_max_tokens(request) == 123456


def test_resolve_max_tokens_uses_generic_default_for_arbitrary_model():
    """任意模型未传输出上限时，应使用统一默认值而非模型映射。"""
    request = OpenAIChatCompletionRequest(model="custom-upstream-model", messages=[])

    assert resolve_max_tokens(request) == 128000


def test_convert_openai_request_preserves_arbitrary_model_for_upstream():
    """转换到上游时应原样保留调用方模型标识。"""
    request = OpenAIChatCompletionRequest(
        model="claude-4.8-opus",
        messages=[{"role": "user", "content": "你好"}],
        max_tokens=123456,
    )

    result = convert_openai_to_claude_request(request)

    assert result["model"] == "claude-4.8-opus"
    assert result["max_tokens"] == 123456


def test_resolve_max_tokens_keeps_client_token_overrides():
    """客户端显式传入 token 上限时，应优先使用客户端设置。"""
    assert (
        resolve_max_tokens(
            OpenAIChatCompletionRequest(
                model="custom-upstream-model", messages=[], max_tokens=100
            )
        )
        == 100
    )
    assert (
        resolve_max_tokens(
            OpenAIChatCompletionRequest(
                model="custom-upstream-model", messages=[], max_completion_tokens=200
            )
        )
        == 200
    )


def test_convert_openai_request_to_claude_request_with_tools_and_tool_results():
    """Chat Completions 请求应转换为 Claude Messages 请求。"""
    request = OpenAIChatCompletionRequest(
        model="custom-upstream-model",
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

    assert result["model"] == "custom-upstream-model"
    assert result["system"] == [
        {
            "type": "text",
            "text": "你是严格的助手。",
        }
    ]
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
        model="custom-upstream-model",
        messages=[{"role": "user", "content": "天气"}],
        max_tokens=64,
    )

    result = convert_claude_response_to_openai(claude_response, request)

    assert result["id"] == "msg_123"
    assert result["object"] == "chat.completion"
    assert result["model"] == "custom-upstream-model"
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


def test_convert_claude_response_marks_tool_calls_even_when_upstream_ends_turn():
    """上游以 end_turn 结束工具调用时，OpenAI 响应仍应标记 tool_calls。"""
    claude_response = {
        "id": "msg_tool_end_turn",
        "content": [
            {
                "type": "tool_use",
                "id": "tool_1",
                "name": "lookup",
                "input": {"query": "天气"},
            }
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    request = OpenAIChatCompletionRequest(
        model="custom-upstream-model",
        messages=[{"role": "user", "content": "天气"}],
        max_tokens=64,
    )

    result = convert_claude_response_to_openai(claude_response, request)

    assert result["choices"][0]["finish_reason"] == "tool_calls"


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
            model="custom-upstream-model",
            messages=[{"role": "user", "content": "你好"}],
            max_tokens=64,
            stream=True,
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


def test_convert_claude_streaming_marks_tool_calls_when_upstream_ends_turn():
    """流式工具调用即使上游以 end_turn 结束也应标记 tool_calls。"""
    async def run_stream_conversion():
        async def claude_stream():
            events = [
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "tool_1",
                        "name": "lookup",
                        "input": {},
                    },
                },
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 1},
                },
            ]
            for event in events:
                yield f"data: {json.dumps(event)}\n\n"

        request = OpenAIChatCompletionRequest(
            model="custom-upstream-model",
            messages=[{"role": "user", "content": "天气"}],
            max_tokens=64,
            stream=True,
        )
        return [chunk async for chunk in convert_claude_streaming_to_openai(claude_stream(), request)]

    chunks = asyncio.run(run_stream_conversion())
    final_chunk = json.loads(chunks[-2].removeprefix("data: "))

    assert final_chunk["choices"][0]["finish_reason"] == "tool_calls"


def test_convert_claude_streaming_logs_empty_response_summary(caplog):
    """空流完成时应记录不含正文的诊断摘要。"""
    async def run_stream_conversion():
        async def claude_stream():
            events = [
                {
                    "type": "message_start",
                    "message": {"usage": {"input_tokens": 3, "output_tokens": 0}},
                },
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 0},
                },
            ]
            for event in events:
                yield f"data: {json.dumps(event)}\n\n"

        request = OpenAIChatCompletionRequest(
            model="Auto", messages=[{"role": "user", "content": "你好"}], max_tokens=64, stream=True
        )
        return [chunk async for chunk in convert_claude_streaming_to_openai(claude_stream(), request)]

    caplog.set_level(logging.INFO, logger="src.conversion.response_converter")
    asyncio.run(run_stream_conversion())

    assert any(
        "openai_stream_conversion_summary" in record.message
        and "content_chunk_count=0" in record.message
        and "finish_reason=stop" in record.message
        for record in caplog.records
    )
