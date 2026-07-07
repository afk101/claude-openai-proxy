"""OpenAI Chat Completions 请求到 Claude Messages 请求的转换。"""

import base64
import json
from typing import Any, Dict, List, Optional, Tuple

from src.core.constants import Constants
from src.models.openai import OpenAIChatCompletionRequest


def convert_openai_to_claude_request(request: OpenAIChatCompletionRequest) -> Dict[str, Any]:
    """将 OpenAI Chat Completions 请求转换为 Claude Messages 请求。"""
    system_messages, conversation_messages = split_system_messages(request.messages)
    claude_request: Dict[str, Any] = {
        "model": request.model,
        "max_tokens": resolve_max_tokens(request),
        "messages": convert_openai_messages(conversation_messages),
        "stream": bool(request.stream),
    }

    system_text = merge_system_messages(system_messages)
    if system_text:
        claude_request["system"] = system_text
    if request.temperature is not None:
        claude_request["temperature"] = request.temperature
    if request.top_p is not None:
        claude_request["top_p"] = request.top_p
    if request.stop is not None:
        claude_request["stop_sequences"] = normalize_stop_sequences(request.stop)
    if request.tools:
        claude_request["tools"] = convert_openai_tools(request.tools)
    tool_choice = convert_tool_choice(request.tool_choice)
    if tool_choice:
        claude_request["tool_choice"] = tool_choice
    if request.metadata:
        claude_request["metadata"] = request.metadata

    return claude_request


def split_system_messages(messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """拆分 system 消息和对话消息。"""
    system_messages = []
    conversation_messages = []
    for message in messages:
        if message.get("role") == Constants.ROLE_SYSTEM:
            system_messages.append(message)
        else:
            conversation_messages.append(message)
    return system_messages, conversation_messages


def resolve_max_tokens(request: OpenAIChatCompletionRequest) -> int:
    """解析 Claude 所需的 max_tokens。"""
    return request.max_tokens or request.max_completion_tokens or Constants.DEFAULT_MAX_TOKENS


def merge_system_messages(messages: List[Dict[str, Any]]) -> Optional[str]:
    """合并 OpenAI system 消息为 Claude 顶层 system。"""
    parts = [content_to_text(message.get("content")) for message in messages]
    text = "\n\n".join(part for part in parts if part)
    return text or None


def convert_openai_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """转换 OpenAI 消息列表为 Claude 消息列表。"""
    return [convert_openai_message(message) for message in messages]


def convert_openai_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """转换单条 OpenAI 消息为 Claude 消息。"""
    role = message.get("role")
    if role == Constants.ROLE_ASSISTANT:
        return convert_assistant_message(message)
    if role == Constants.ROLE_TOOL:
        return convert_tool_message(message)
    return {"role": Constants.ROLE_USER, "content": convert_user_content(message.get("content"))}


def convert_user_content(content: Any) -> Any:
    """转换 user 消息内容。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return [convert_content_part(part) for part in content if convert_content_part(part)]
    return str(content)


def convert_content_part(part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """转换 OpenAI 多模态内容片段。"""
    part_type = part.get("type")
    if part_type == Constants.OPENAI_CONTENT_TEXT:
        return {"type": Constants.CONTENT_TEXT, "text": part.get("text", "")}
    if part_type == Constants.OPENAI_CONTENT_IMAGE_URL:
        return convert_image_part(part)
    return None


def convert_image_part(part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """转换 OpenAI image_url 为 Claude image。"""
    image_url = part.get("image_url") or {}
    url = image_url.get("url", "") if isinstance(image_url, dict) else ""
    media_type, data = parse_data_url(url)
    if not media_type or not data:
        return None
    return {
        "type": Constants.CONTENT_IMAGE,
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def parse_data_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """解析 data URL，返回媒体类型和 base64 数据。"""
    if not url.startswith("data:") or ";base64," not in url:
        return None, None
    header, data = url.split(",", 1)
    media_type = header.removeprefix("data:").removesuffix(";base64")
    try:
        base64.b64decode(data, validate=False)
    except Exception:
        return None, None
    return media_type, data


def convert_assistant_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """转换 assistant 消息，包含文本、推理内容和工具调用。"""
    content_blocks: List[Dict[str, Any]] = []
    reasoning_content = message.get("reasoning_content")
    if reasoning_content:
        content_blocks.append({"type": Constants.CONTENT_THINKING, "thinking": reasoning_content})

    text = content_to_text(message.get("content"))
    if text:
        content_blocks.append({"type": Constants.CONTENT_TEXT, "text": text})

    for tool_call in message.get("tool_calls") or []:
        content_blocks.append(convert_tool_call(tool_call))

    return {"role": Constants.ROLE_ASSISTANT, "content": content_blocks or ""}


def convert_tool_call(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    """转换 OpenAI tool_call 为 Claude tool_use。"""
    function_data = tool_call.get(Constants.TOOL_FUNCTION) or {}
    return {
        "type": Constants.CONTENT_TOOL_USE,
        "id": tool_call.get("id", ""),
        "name": function_data.get("name", ""),
        "input": parse_json_object(function_data.get("arguments")),
    }


def convert_tool_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """转换 OpenAI tool 消息为 Claude user/tool_result 消息。"""
    return {
        "role": Constants.ROLE_USER,
        "content": [
            {
                "type": Constants.CONTENT_TOOL_RESULT,
                "tool_use_id": message.get("tool_call_id", ""),
                "content": content_to_text(message.get("content")),
            }
        ],
    }


def parse_json_object(value: Any) -> Dict[str, Any]:
    """解析 JSON 对象，失败时保留原始参数。"""
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        return {"raw_arguments": value}


def content_to_text(content: Any) -> str:
    """将 OpenAI 内容归一化为文本，支持复杂结构（list/dict/嵌套）。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _content_list_to_text(content)
    if isinstance(content, dict):
        return _content_dict_to_text(content)
    try:
        return str(content)
    except Exception:
        return "Unparseable content"


def _content_list_to_text(items: list) -> str:
    """将列表内容转换为文本，递归处理每个元素。"""
    parts: list = []
    for item in items:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            parts.append(_content_dict_to_text(item))
        else:
            try:
                parts.append(json.dumps(item, ensure_ascii=False))
            except (TypeError, ValueError):
                parts.append(str(item))
    return "\n".join(part for part in parts if part)


def _content_dict_to_text(item: dict) -> str:
    """将字典内容转换为文本。"""
    if item.get("type") == Constants.OPENAI_CONTENT_TEXT:
        return item.get("text", "")
    if "text" in item:
        return str(item["text"])
    try:
        return json.dumps(item, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(item)


def normalize_stop_sequences(stop: Any) -> List[str]:
    """将 OpenAI stop 参数转换为 Claude stop_sequences。"""
    if isinstance(stop, list):
        return [str(item) for item in stop]
    return [str(stop)]


def convert_openai_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """转换 OpenAI tools 为 Claude tools。"""
    claude_tools = []
    for tool in tools:
        if tool.get("type") != Constants.TOOL_FUNCTION:
            continue
        function_data = tool.get(Constants.TOOL_FUNCTION) or {}
        claude_tools.append(
            {
                "name": function_data.get("name", ""),
                "description": function_data.get("description", ""),
                "input_schema": normalize_tool_schema(function_data.get("parameters")),
            }
        )
    return claude_tools


def normalize_tool_schema(schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """规范化 Claude 工具 input_schema。"""
    normalized = dict(schema or {})
    normalized.setdefault("type", "object")
    normalized.setdefault("properties", {})
    return normalized


def convert_tool_choice(tool_choice: Any) -> Optional[Dict[str, Any]]:
    """转换 OpenAI tool_choice 为 Claude tool_choice。"""
    if tool_choice in (None, "none"):
        return None
    if tool_choice == "auto":
        return {"type": "auto"}
    if tool_choice == "required":
        return {"type": "any"}
    if isinstance(tool_choice, dict):
        function_data = tool_choice.get(Constants.TOOL_FUNCTION) or {}
        name = function_data.get("name")
        if name:
            return {"type": "tool", "name": name}
    return None
