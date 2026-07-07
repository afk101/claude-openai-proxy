"""运行配置。"""

import os

from src.core.constants import Constants


class Config:
    """从环境变量读取代理服务配置。"""

    def __init__(self) -> None:
        self.claude_api_key = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        self.claude_base_url = os.environ.get("CLAUDE_BASE_URL", "https://api.anthropic.com")
        self.anthropic_version = os.environ.get("ANTHROPIC_VERSION", "2023-06-01")
        self.client_api_key = os.environ.get("PROXY_API_KEY")
        self.host = os.environ.get("HOST", "0.0.0.0")
        self.port = int(os.environ.get("PORT", str(Constants.DEFAULT_PORT)))
        self.log_level = os.environ.get("LOG_LEVEL", "INFO")
        self.request_timeout = int(os.environ.get("REQUEST_TIMEOUT", "90"))
        self.read_timeout = int(os.environ.get("READ_TIMEOUT", "480"))

    def validate_client_api_key(self, client_api_key: str) -> bool:
        """校验客户端访问代理时提供的 API Key。"""
        if not self.client_api_key:
            return True
        return client_api_key == self.client_api_key


config = Config()
