"""最终产品名称、配置样例和启动入口的公开契约测试。"""

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

import src.main as main_module
from src.main import app

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_http_metadata_describes_only_responses_proxy():
    """根路径、健康检查和 OpenAPI 只能声明真实存在的 Responses 产品面。"""
    client = TestClient(app)

    root_response = client.get("/")
    health_response = client.get("/health")
    schema = client.get("/openapi.json").json()

    assert root_response.status_code == 200
    assert root_response.json()["endpoints"] == {
        "responses": "/v1/responses",
        "health": "/health",
    }
    assert health_response.status_code == 200
    assert set(health_response.json()) == {
        "status",
        "timestamp",
        "upstream_base_url_configured",
        "ordinary_api_key_configured",
        "client_api_key_validation",
    }
    assert isinstance(
        health_response.json()["upstream_base_url_configured"], bool
    )
    assert schema["info"]["title"] == "OpenAI Responses Proxy"
    assert "/v1/responses" in schema["paths"]
    assert "/v1/chat/completions" not in schema["paths"]


def test_cli_help_explains_responses_endpoint_and_fallback_configuration(
    monkeypatch, capsys
):
    """CLI help 应准确区分必填服务根地址和仅供未命中模型使用的普通密钥。"""
    monkeypatch.setattr(sys, "argv", ["openai-responses-proxy", "--help"])

    main_module.main()

    output = capsys.readouterr().out
    assert "OpenAI Responses Proxy" in output
    assert "POST /v1/responses" in output
    assert "CLAUDE_BASE_URL" in output
    assert "CLAUDE_API_KEY" in output
    assert "ANTHROPIC_API_KEY" in output
    assert "fallback" in output.lower()
    assert "OpenAI-to-Claude" not in output
    assert "Chat Completions" not in output


def test_distribution_and_console_script_use_responses_product_name():
    """构建元数据与锁文件必须只发布新的 Responses 命令身份。"""
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lockfile = (PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8")

    assert 'name = "openai-responses-proxy"' in pyproject
    assert 'openai-responses-proxy = "src.main:main"' in pyproject
    assert 'name = "openai-responses-proxy"' in lockfile
    assert 'name = "claude-openai-proxy"' not in pyproject
    assert 'claude-openai-proxy = "src.main:main"' not in pyproject
    assert 'name = "claude-openai-proxy"' not in lockfile


def test_readme_and_env_example_match_runtime_contract():
    """用户文档必须给出可复制的 Responses 启动、配置和两种 curl 调用。"""
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    start_script = PROJECT_ROOT / "start.sh"

    assert os.access(start_script, os.X_OK)
    assert "./start.sh" in readme
    assert "修改 `.env` 后需要重启" in readme
    assert "https://code.jizhi.360.cn/aiproxy" in readme
    assert readme.count("/v1/responses") >= 2
    assert '"stream": true' in readme
    assert "目录中完全未出现" in readme
    assert "CLAUDE_API_KEY" in readme
    assert "ANTHROPIC_API_KEY" in readme
    assert "PROXY_API_KEY" in readme
    assert "/v1/chat/completions" not in readme
    assert "ANTHROPIC_VERSION" not in readme
    assert "OpenAI-to-Claude" not in readme

    assert 'CLAUDE_BASE_URL="https://code.jizhi.360.cn/aiproxy"' in env_example
    assert 'PORT="7072"' in env_example
    assert "目录中完全未出现" in env_example
    assert "ANTHROPIC_API_KEY" in env_example
    assert "PROXY_API_KEY" in env_example
    assert "ANTHROPIC_VERSION" not in env_example
    assert "8000" not in env_example
