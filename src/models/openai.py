"""OpenAI Chat Completions 数据模型。"""

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

OpenAIMessageContent = Union[str, List[Dict[str, Any]], None]


class OpenAIChatCompletionRequest(BaseModel):
    """Chat Completions 请求模型。"""

    model: str
    messages: List[Dict[str, Any]]
    max_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    stop: Optional[Union[str, List[str]]] = None
    stream: Optional[bool] = False
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    user: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    stream_options: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="allow")


class OpenAIChatCompletionResponseFormat(BaseModel):
    """响应格式占位模型，保留扩展字段。"""

    type: str = Field(default="text")

    model_config = ConfigDict(extra="allow")
