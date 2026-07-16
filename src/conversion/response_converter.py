"""Claude Messages 响应到 OpenAI Chat Completions 响应的转换。"""

import json
import logging
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from src.core.constants import Constants
from src.models.openai import OpenAIChatCompletionRequest

logger = logging.getLogger(__name__)


def convert_claude_response_to_openai(
    claude_response: Dict[str, Any], original_request: OpenAIChatCompletionRequest
) -> Dict[str, Any]:
    """将 Claude Messages 非流式响应转换为 OpenAI Chat Completions 响应。"""
    content_blocks = claude_response.get("content") or []
    message = build_openai_message(content_blocks)
    usage = convert_usage(claude_response.get("usage") or {})

    return {
        "id": claude_response.get("id") or f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": original_request.model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": resolve_finish_reason(claude_response, message),
            }
        ],
        "usage": usage,
    }


def resolve_finish_reason(
    claude_response: Dict[str, Any], message: Dict[str, Any]
) -> str:
    """根据工具调用内容和上游停止原因确定 OpenAI 结束原因。"""
    if message.get("tool_calls"):
        return Constants.FINISH_TOOL_CALLS
    return map_stop_reason_to_finish_reason(claude_response.get("stop_reason"))


def build_openai_message(content_blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """根据 Claude content blocks 构造 OpenAI assistant message。"""
    text_parts: List[str] = []
    thinking_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []

    for block in content_blocks:
        block_type = block.get("type")
        if block_type == Constants.CONTENT_TEXT:
            text_parts.append(block.get("text", ""))
        elif block_type == Constants.CONTENT_THINKING:
            thinking_parts.append(block.get("thinking", ""))
        elif block_type == Constants.CONTENT_TOOL_USE:
            tool_calls.append(convert_tool_use_to_tool_call(block))

    message: Dict[str, Any] = {
        "role": Constants.ROLE_ASSISTANT,
        "content": "".join(text_parts) if text_parts else None,
    }
    if thinking_parts:
        message["reasoning_content"] = "".join(thinking_parts)
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def convert_tool_use_to_tool_call(block: Dict[str, Any]) -> Dict[str, Any]:
    """将 Claude tool_use 内容块转换为 OpenAI tool_call。"""
    return {
        "id": block.get("id") or f"call_{uuid.uuid4().hex}",
        "type": Constants.TOOL_FUNCTION,
        Constants.TOOL_FUNCTION: {
            "name": block.get("name", ""),
            "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False, separators=(",", ":")),
        },
    }


def convert_usage(usage: Dict[str, Any]) -> Dict[str, int]:
    """转换 Claude usage 为 OpenAI usage。"""
    prompt_tokens = int(usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("output_tokens") or 0)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def map_stop_reason_to_finish_reason(stop_reason: Optional[str]) -> str:
    """将 Claude stop_reason 映射为 OpenAI finish_reason。"""
    mapping = {
        Constants.STOP_END_TURN: Constants.FINISH_STOP,
        Constants.STOP_MAX_TOKENS: Constants.FINISH_LENGTH,
        Constants.STOP_TOOL_USE: Constants.FINISH_TOOL_CALLS,
    }
    return mapping.get(stop_reason or Constants.STOP_END_TURN, Constants.FINISH_STOP)


async def convert_claude_streaming_to_openai(
    claude_stream: AsyncGenerator[str, None],
    original_request: OpenAIChatCompletionRequest,
    request_id: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """将 Claude SSE 流转换为 OpenAI Chat Completions SSE 流。"""
    state = create_stream_state(original_request)
    try:
        yield format_sse_chunk(build_chunk(state, {"role": Constants.ROLE_ASSISTANT}))

        async for line in claude_stream:
            async for chunk in convert_claude_sse_line(line, state):
                yield chunk

        if not state["finished"]:
            state["finish_reason"] = Constants.FINISH_STOP
            yield format_sse_chunk(build_final_chunk(state, Constants.FINISH_STOP))
        yield "data: [DONE]\n\n"
    finally:
        log_stream_conversion_summary(state, request_id)


def create_stream_state(original_request: OpenAIChatCompletionRequest) -> Dict[str, Any]:
    """创建流式转换状态。"""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "model": original_request.model,
        "created": int(time.time()),
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "tool_calls": {},
        "finished": False,
        "upstream_event_count": 0,
        "upstream_event_types": {},
        "content_chunk_count": 0,
        "reasoning_chunk_count": 0,
        "tool_call_chunk_count": 0,
        "finish_reason": None,
        "upstream_stop_reason": None,
    }


async def convert_claude_sse_line(
    line: str, state: Dict[str, Any]
) -> AsyncGenerator[str, None]:
    """转换单段 Claude SSE 文本。"""
    event = parse_sse_event(line)
    if not event:
        return

    event_type = event.get("type")
    record_upstream_stream_event(state, event_type)
    if event_type == Constants.EVENT_MESSAGE_START:
        update_usage_from_message_start(event, state)
    elif event_type == Constants.EVENT_CONTENT_BLOCK_START:
        chunk = convert_content_block_start(event, state)
        if chunk:
            yield format_sse_chunk(chunk)
    elif event_type == Constants.EVENT_CONTENT_BLOCK_DELTA:
        chunk = convert_content_block_delta(event, state)
        if chunk:
            record_converted_stream_delta(state, event)
            yield format_sse_chunk(chunk)
    elif event_type == Constants.EVENT_MESSAGE_DELTA:
        update_usage_from_message_delta(event, state)
        finish_reason = resolve_stream_finish_reason(event, state)
        state["finish_reason"] = finish_reason
        state["upstream_stop_reason"] = (event.get("delta") or {}).get("stop_reason")
        state["finished"] = True
        yield format_sse_chunk(build_final_chunk(state, finish_reason))


def parse_sse_event(line: str) -> Optional[Dict[str, Any]]:
    """解析 Claude SSE 文本中的 data JSON。"""
    for part in line.splitlines():
        if not part.startswith("data: "):
            continue
        data = part.removeprefix("data: ").strip()
        if not data or data == "[DONE]":
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None
    return None


def resolve_stream_finish_reason(event: Dict[str, Any], state: Dict[str, Any]) -> str:
    """根据流式工具调用状态和上游停止原因确定结束原因。"""
    if state["tool_calls"]:
        return Constants.FINISH_TOOL_CALLS
    return map_stop_reason_to_finish_reason((event.get("delta") or {}).get("stop_reason"))


def record_upstream_stream_event(state: Dict[str, Any], event_type: Any) -> None:
    """记录上游 SSE 事件类型统计，不保存事件正文。"""
    normalized_type = str(event_type or "unknown")
    state["upstream_event_count"] += 1
    state["upstream_event_types"][normalized_type] = (
        state["upstream_event_types"].get(normalized_type, 0) + 1
    )


def record_converted_stream_delta(state: Dict[str, Any], event: Dict[str, Any]) -> None:
    """记录转换出的内容类别数量，不保存内容数据。"""
    delta_type = (event.get("delta") or {}).get("type")
    if delta_type == Constants.DELTA_TEXT:
        state["content_chunk_count"] += 1
    elif delta_type == Constants.DELTA_THINKING:
        state["reasoning_chunk_count"] += 1
    elif delta_type == Constants.DELTA_INPUT_JSON:
        state["tool_call_chunk_count"] += 1


def log_stream_conversion_summary(state: Dict[str, Any], request_id: Optional[str]) -> None:
    """输出流式转换的脱敏诊断摘要。"""
    logger.info(
        "openai_stream_conversion_summary request_id=%s upstream_event_count=%s "
        "upstream_event_types=%s content_chunk_count=%s reasoning_chunk_count=%s "
        "tool_call_chunk_count=%s finish_reason=%s upstream_stop_reason=%s "
        "prompt_tokens=%s completion_tokens=%s finished=%s",
        request_id,
        state["upstream_event_count"],
        state["upstream_event_types"],
        state["content_chunk_count"],
        state["reasoning_chunk_count"],
        state["tool_call_chunk_count"],
        state["finish_reason"],
        state["upstream_stop_reason"],
        state["prompt_tokens"],
        state["completion_tokens"],
        state["finished"],
    )


def update_usage_from_message_start(event: Dict[str, Any], state: Dict[str, Any]) -> None:
    """从 message_start 更新输入 token。"""
    message = event.get("message") or {}
    usage = message.get("usage") or {}
    state["prompt_tokens"] = int(usage.get("input_tokens") or state["prompt_tokens"])


def update_usage_from_message_delta(event: Dict[str, Any], state: Dict[str, Any]) -> None:
    """从 message_delta 更新输出 token。"""
    usage = event.get("usage") or {}
    state["completion_tokens"] = int(usage.get("output_tokens") or state["completion_tokens"])


def convert_content_block_start(event: Dict[str, Any], state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """转换 content_block_start 事件。"""
    content_block = event.get("content_block") or {}
    if content_block.get("type") != Constants.CONTENT_TOOL_USE:
        return None

    index = int(event.get("index") or 0)
    state["tool_calls"][index] = {
        "id": content_block.get("id") or f"call_{uuid.uuid4().hex}",
        "name": content_block.get("name", ""),
    }
    return build_chunk(
        state,
        {
            "tool_calls": [
                {
                    "index": index,
                    "id": state["tool_calls"][index]["id"],
                    "type": Constants.TOOL_FUNCTION,
                    Constants.TOOL_FUNCTION: {
                        "name": state["tool_calls"][index]["name"],
                        "arguments": "",
                    },
                }
            ]
        },
    )


def convert_content_block_delta(event: Dict[str, Any], state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """转换 content_block_delta 事件。"""
    delta = event.get("delta") or {}
    delta_type = delta.get("type")
    if delta_type == Constants.DELTA_TEXT:
        return build_chunk(state, {"content": delta.get("text", "")})
    if delta_type == Constants.DELTA_THINKING:
        return build_chunk(state, {"reasoning_content": delta.get("thinking", "")})
    if delta_type == Constants.DELTA_INPUT_JSON:
        index = int(event.get("index") or 0)
        return build_chunk(
            state,
            {
                "tool_calls": [
                    {
                        "index": index,
                        Constants.TOOL_FUNCTION: {"arguments": delta.get("partial_json", "")},
                    }
                ]
            },
        )
    return None


def build_chunk(state: Dict[str, Any], delta: Dict[str, Any]) -> Dict[str, Any]:
    """构造 OpenAI stream chunk。"""
    return {
        "id": state["id"],
        "object": "chat.completion.chunk",
        "created": state["created"],
        "model": state["model"],
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }


def build_final_chunk(state: Dict[str, Any], finish_reason: str) -> Dict[str, Any]:
    """构造 OpenAI stream 结束 chunk。"""
    chunk = build_chunk(state, {})
    chunk["choices"][0]["finish_reason"] = finish_reason
    chunk["usage"] = {
        "prompt_tokens": state["prompt_tokens"],
        "completion_tokens": state["completion_tokens"],
        "total_tokens": state["prompt_tokens"] + state["completion_tokens"],
    }
    return chunk


def format_sse_chunk(chunk: Dict[str, Any]) -> str:
    """格式化 OpenAI SSE chunk。"""
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
