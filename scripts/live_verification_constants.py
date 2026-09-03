"""真实 Responses 验收工具使用的固定模型、地址和微小自包含资源。"""

MODEL_MATRIX = (
    "WisGPT-5.6-Terra",
    "WisGPT-5.6-Luna",
    "WisGPT-5.6-Sol",
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v4-pro",
    "openai/gpt-5.6-sol-cpr",
    "openai/gpt-5.6-terra-cpr",
    "openai/gpt-5.6-luna-cpr",
    "z-ai/glm-5.3-flash",
    "qwen/qwen3.8-flash",
    "360-Wiscode-Multimodal",
)
MESSAGE_MATRIX_MODEL = "z-ai/glm-5.3-flash"

PROXY_BASE_URL_ENV = "RESPONSES_PROXY_BASE_URL"
PROXY_API_KEY_ENV = "PROXY_API_KEY"
DEFAULT_PROXY_BASE_URL = "http://127.0.0.1:7072"
RESPONSES_CREATE_PATH = "/v1/responses"
DEFAULT_TIMEOUT_SECONDS = 120.0
# GLM 会先消耗 reasoning tokens；1024 可避免短回答在正文产生前被 128 token 截断。
DEFAULT_MAX_OUTPUT_TOKENS = 1024
RESULT_TEXT_LIMIT = 240

FUNCTION_TOOL_NAME = "get_test_value"
FUNCTION_ARGUMENT_KEY = "test"
FUNCTION_OUTPUT = "TEST OK"
FUNCTION_FINAL_MARKER = "FUNCTION OK"
STREAM_FINAL_MARKER = "STREAM OK"
FILE_CONTENT_MARKER = "FILE CONTENT OK"
FILE_NAME = "verification.pdf"
ROUTE_LOG_PATTERN = (
    r"\bresponses_(?:completed|stream_started)\b.*\bmodel=(?P<model>\S+)\s+"
    r"route_type=(?P<route>package|ordinary)\b"
)
SENSITIVE_OUTPUT_PATTERNS = (
    r"(?i)Bearer\s+[^\s,;]+",
    r"sk-[A-Za-z0-9_-]{8,}",
    r"eyJ[A-Za-z0-9_-]{16,}(?:\.[A-Za-z0-9_-]+){1,2}",
)

# 16x16 的纯红 RGB PNG。资源足够小，并且不依赖任何第三方 URL。
RED_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAF0lEQVR4nGP4z8BAEiJN9aiG"
    "UQ1DSgMAkPn/Afnh+ngAAAAASUVORK5CYII="
)
