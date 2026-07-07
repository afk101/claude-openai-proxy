"""Claude Messages 数据模型。"""

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict


class ClaudeContentBlockText(BaseModel):
    """Claude 文本内容块。"""

    type: Literal["text"]
    text: str


class ClaudeContentBlockThinking(BaseModel):
    """Claude thinking 内容块。"""

    type: Literal["thinking"]
    thinking: str


class ClaudeContentBlockImage(BaseModel):
    """Claude 图片内容块。"""

    type: Literal["image"]
    source: Dict[str, Any]


class ClaudeContentBlockToolUse(BaseModel):
    """Claude 工具调用内容块。"""

    type: Literal["tool_use"]
    id: str
    name: str
    input: Dict[str, Any]


class ClaudeContentBlockToolResult(BaseModel):
    """Claude 工具结果内容块。"""

    type: Literal["tool_result"]
    tool_use_id: str
    content: Union[str, List[Dict[str, Any]], Dict[str, Any]]


ClaudeContentBlock = Union[
    ClaudeContentBlockText,
    ClaudeContentBlockThinking,
    ClaudeContentBlockImage,
    ClaudeContentBlockToolUse,
    ClaudeContentBlockToolResult,
]


class ClaudeMessage(BaseModel):
    """Claude message 条目。"""

    role: Literal["user", "assistant"]
    content: Union[str, List[ClaudeContentBlock]]


class ClaudeTool(BaseModel):
    """Claude 工具定义。"""

    name: str
    description: Optional[str] = None
    input_schema: Dict[str, Any]


class ClaudeMessagesRequest(BaseModel):
    """Claude Messages 请求模型。"""

    model: str
    max_tokens: int
    messages: List[ClaudeMessage]
    system: Optional[Union[str, List[Dict[str, Any]]]] = None
    stop_sequences: Optional[List[str]] = None
    stream: Optional[bool] = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None
    tools: Optional[List[ClaudeTool]] = None
    tool_choice: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="allow")
