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

    DEFAULT_MAX_TOKENS = 64000
    DEFAULT_PORT = 7072
    MAX_ERROR_LOG_DETAIL_LENGTH = 1000

    HEADER_CLIENT_SOURCE = "x-src"
    HEADER_CLIENT_TASK_ID = "x-client-task-id"
    HEADER_CLIENT_TRACE_ID = "x-client-trace-id"
    CLIENT_SOURCE_IDE = "ide"

    STREAM_DONE_EVENT = "data: [DONE]\n\n"
    STREAM_ERROR_TYPE = "upstream_stream_error"
    STREAM_ERROR_TIMEOUT_CODE = "upstream_timeout"
    STREAM_ERROR_CONNECTION_CODE = "upstream_connection_error"
    STREAM_ERROR_CONVERSION_CODE = "stream_conversion_error"
    STREAM_ERROR_TIMEOUT_MESSAGE = "上游流式响应超时，请稍后重试。"
    STREAM_ERROR_CONNECTION_MESSAGE = "上游流式连接中断，请稍后重试。"
    STREAM_ERROR_CONVERSION_MESSAGE = "流式响应转换失败，请稍后重试。"
