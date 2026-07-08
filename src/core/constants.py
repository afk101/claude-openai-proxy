"""项目常量定义。"""


class Constants:
    """集中维护 OpenAI 与 Claude 转换使用的常量。"""

    ROLE_SYSTEM = "system"
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLE_TOOL = "tool"

    CONTENT_TEXT = "text"
    CONTENT_IMAGE = "image"
    CONTENT_THINKING = "thinking"
    CONTENT_TOOL_USE = "tool_use"
    CONTENT_TOOL_RESULT = "tool_result"

    OPENAI_CONTENT_TEXT = "text"
    OPENAI_CONTENT_IMAGE_URL = "image_url"
    TOOL_FUNCTION = "function"

    STOP_END_TURN = "end_turn"
    STOP_MAX_TOKENS = "max_tokens"
    STOP_TOOL_USE = "tool_use"

    FINISH_STOP = "stop"
    FINISH_LENGTH = "length"
    FINISH_TOOL_CALLS = "tool_calls"

    EVENT_MESSAGE_START = "message_start"
    EVENT_MESSAGE_DELTA = "message_delta"
    EVENT_MESSAGE_STOP = "message_stop"
    EVENT_CONTENT_BLOCK_START = "content_block_start"
    EVENT_CONTENT_BLOCK_DELTA = "content_block_delta"
    EVENT_CONTENT_BLOCK_STOP = "content_block_stop"

    DELTA_TEXT = "text_delta"
    DELTA_THINKING = "thinking_delta"
    DELTA_INPUT_JSON = "input_json_delta"

    DEFAULT_MAX_TOKENS = 4096
    DEFAULT_PORT = 7072

    MODEL_MAX_TOKENS_MAP = {
        "claude-fable-5": 128000,
        "claude-mythos-5": 128000,
        "claude-opus-4-8": 128000,
        "claude-opus-4-7": 128000,
        "claude-sonnet-5": 128000,
        "claude-sonnet-4-6": 64000,
        "claude-haiku-4-5": 64000,
        "claude-haiku-4-5-20251001": 64000,
        "claude-3-5-sonnet-20241022": 8192,
        "claude-3-5-sonnet-20240620": 8192,
        "claude-3-5-haiku-20241022": 8192,
        "claude-3-opus-20240229": 4096,
        "claude-3-sonnet-20240229": 4096,
        "claude-3-haiku-20240307": 4096,
    }
