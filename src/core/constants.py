"""项目常量定义。"""


class Constants:
    """集中维护 Responses 代理的路径、Header 与稳定错误信息。"""

    DEFAULT_PORT = 7072

    HEADER_CLIENT_SOURCE = "x-src"
    HEADER_CLIENT_TASK_ID = "x-client-task-id"
    HEADER_CLIENT_TRACE_ID = "x-client-trace-id"
    CLIENT_SOURCE_IDE = "ide"

    RESPONSES_CREATE_PATH = "/v1/responses"
    RESPONSES_HTTP_METHOD = "POST"
    RESPONSES_CONTENT_TYPE = "application/json"
    RESPONSES_DEFAULT_ACCEPT_ENCODING = "identity"
    RESPONSES_ROUTE_TYPE_PACKAGE = "package"
    RESPONSES_ROUTE_TYPE_ORDINARY = "ordinary"
    RESPONSES_INVALID_JSON_DETAIL = "Responses 请求体必须是合法 JSON object"
    RESPONSES_INVALID_MODEL_DETAIL = "Responses 请求的 model 必须是非空字符串"
    RESPONSES_INVALID_STREAM_DETAIL = "Responses 请求的 stream 必须是 boolean"
    RESPONSES_DUPLICATE_MODEL_DETAIL = "Responses 请求不能包含重复的顶层 model"
    RESPONSES_DUPLICATE_STREAM_DETAIL = "Responses 请求不能包含重复的顶层 stream"
    RESPONSES_INVALID_BASE_URL_DETAIL = "CLAUDE_BASE_URL 配置缺失或格式非法"
    RESPONSES_MISSING_API_KEY_DETAIL = (
        "普通上游密钥未配置，请设置 CLAUDE_API_KEY 或 ANTHROPIC_API_KEY"
    )
    RESPONSES_UPSTREAM_TIMEOUT_DETAIL = "连接上游 Responses 服务超时，请稍后重试"
    RESPONSES_UPSTREAM_CONNECTION_DETAIL = "连接上游 Responses 服务失败，请稍后重试"

    HEADER_AUTHORIZATION = "authorization"
    HEADER_CONTENT_TYPE = "content-type"
    HEADER_ACCEPT_ENCODING = "accept-encoding"
    HEADER_REQUEST_ID = "x-request-id"
    HEADER_API_KEY = "x-api-key"
    RESPONSE_FILTERED_HEADERS = (
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "server",
        "date",
    )

    ZQI_CACHE_TTL_SECONDS = 1800
    ZQI_REQUEST_TIMEOUT_SECONDS = 10
    ZQI_RETRY_BACKOFF_SECONDS = 0.2
    ZQI_MAX_RETRIES = 1
    ZQI_FORWARD_URL = "https://llm.api.zyuncs.com/v1"
    ZQI_INTERNAL_IDENTIFIER = "zyzj_package"
    ZQI_EXTERNAL_IDENTIFIER = "sfdj_package"
    ZQI_API_NAME_RESPONSES = "responses"
