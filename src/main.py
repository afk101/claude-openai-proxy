"""应用入口。"""

import logging
import sys

import uvicorn
from fastapi import FastAPI

from src.api.endpoints import router as api_router
from src.core.config import config
from src.core.constants import Constants

app = FastAPI(title=Constants.APP_NAME, version=Constants.APP_VERSION)
app.include_router(api_router)


def main() -> None:
    """启动代理服务。"""
    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print(f"{Constants.APP_NAME} v{Constants.APP_VERSION}")
        print("Usage: python -m src.main")
        print(f"Endpoint: POST {Constants.RESPONSES_CREATE_PATH}")
        print(f"Endpoint: GET {Constants.MODELS_LIST_PATH}")
        print("Required upstream root before /v1: CLAUDE_BASE_URL")
        print("Ordinary fallback key: CLAUDE_API_KEY or ANTHROPIC_API_KEY")
        return

    log_level = config.log_level.split()[0].lower()
    if log_level not in {"debug", "info", "warning", "error", "critical"}:
        log_level = "info"

    logging.basicConfig(level=log_level.upper())
    uvicorn.run("src.main:app", host=config.host, port=config.port, log_level=log_level)


if __name__ == "__main__":
    main()
