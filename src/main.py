"""应用入口。"""

import logging
import sys

import uvicorn
from fastapi import FastAPI

from src.api.endpoints import router as api_router
from src.core.config import config

app = FastAPI(title="OpenAI-to-Claude API Proxy", version="1.0.0")
app.include_router(api_router)


def main() -> None:
    """启动代理服务。"""
    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print("OpenAI-to-Claude API Proxy v1.0.0")
        print("Usage: python -m src.main")
        print("Required for real upstream calls: CLAUDE_API_KEY or ANTHROPIC_API_KEY")
        return

    log_level = config.log_level.split()[0].lower()
    if log_level not in {"debug", "info", "warning", "error", "critical"}:
        log_level = "info"

    logging.basicConfig(level=log_level.upper())
    uvicorn.run("src.main:app", host=config.host, port=config.port, log_level=log_level)


if __name__ == "__main__":
    main()
