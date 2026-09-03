"""运行配置。"""

import os

from dotenv import load_dotenv

from src.core.constants import Constants

# 加载项目根目录 .env 文件中的环境变量（如果存在）。
# 注意：已有的环境变量不会被覆盖；加载职责与配置构造放在同一模块。
load_dotenv()


class Config:
    """从环境变量读取代理服务配置。"""

    def __init__(self) -> None:
        self.upstream_api_key = os.environ.get(
            Constants.ENV_UPSTREAM_API_KEY
        ) or os.environ.get(Constants.ENV_UPSTREAM_API_KEY_COMPAT)
        # Responses 入口必须显式配置服务根地址，不能回退到其他协议的默认上游。
        self.upstream_base_url = os.environ.get(Constants.ENV_UPSTREAM_BASE_URL)
        self.client_api_key = os.environ.get(Constants.ENV_PROXY_API_KEY)
        self.host = os.environ.get(Constants.ENV_HOST, Constants.DEFAULT_HOST)
        self.port = int(os.environ.get(Constants.ENV_PORT, str(Constants.DEFAULT_PORT)))
        self.log_level = os.environ.get(
            Constants.ENV_LOG_LEVEL,
            Constants.DEFAULT_LOG_LEVEL,
        )
        self.request_timeout = int(
            os.environ.get(
                Constants.ENV_REQUEST_TIMEOUT,
                str(Constants.DEFAULT_REQUEST_TIMEOUT_SECONDS),
            )
        )
        self.read_timeout = int(
            os.environ.get(
                Constants.ENV_READ_TIMEOUT,
                str(Constants.DEFAULT_READ_TIMEOUT_SECONDS),
            )
        )

    def validate_client_api_key(self, client_api_key: str) -> bool:
        """校验客户端访问代理时提供的 API Key。"""
        if not self.client_api_key:
            return True
        return client_api_key == self.client_api_key


config = Config()
