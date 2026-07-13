# Claude OpenAI Proxy

OpenAI Chat Completions 兼容入口，用来调用 Claude Messages 兼容服务。代理会把客户端的 `/v1/chat/completions` 请求转换为 Claude Messages 请求，再把 Claude 响应转换回 OpenAI Chat Completions 响应。

## 功能

- 支持 `POST /v1/chat/completions`
- 支持非流式响应转换为 `chat.completion`
- 支持 Claude SSE 流式响应转换为 `chat.completion.chunk`
- 支持 system、user、assistant、tool 消息转换
- 支持 OpenAI tools/tool_choice 与 Claude tools/tool_choice 转换
- 支持 OpenAI 多模态 `image_url` data URL 转 Claude base64 image
- 支持 Claude thinking 转 OpenAI `reasoning_content`

## 安装

推荐使用 `uv` 管理虚拟环境和依赖：

```bash
uv venv
source .venv/bin/activate
uv sync
```

如果要运行测试，安装开发依赖：

```bash
uv sync --dev
```

也可以不手动激活虚拟环境，直接使用 `uv run`：

```bash
uv sync
uv run python -m src.main
```

如果不用 `uv`，可以用 `pip` 安装依赖：

```bash
python -m pip install -r requirements.txt
```

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CLAUDE_API_KEY` | 空 | 上游 Claude 兼容服务密钥 |
| `ANTHROPIC_API_KEY` | 空 | `CLAUDE_API_KEY` 未设置时使用 |
| `CLAUDE_BASE_URL` | `https://api.anthropic.com` | 上游 Claude 兼容服务地址 |
| `ANTHROPIC_VERSION` | `2023-06-01` | Anthropic API 版本请求头 |
| `PROXY_API_KEY` | 空 | 可选，设置后客户端必须提供同值 API Key |
| `HOST` | `0.0.0.0` | 服务监听地址 |
| `PORT` | `7072` | 服务监听端口 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `REQUEST_TIMEOUT` | `90` | 请求连接/写入超时秒数 |
| `READ_TIMEOUT` | `480` | 流式读取超时秒数 |

模型名称会原样透传给上游。调用方指定 `max_tokens` 或 `max_completion_tokens` 时，代理会使用该值；两者均未指定时，统一默认 `200000`。

## 启动

方式一：使用 `uv run` 启动，推荐用于避免跑到错误的 Python 环境：

```bash
CLAUDE_BASE_URL="支持claude response协议的baseurl" \
ANTHROPIC_API_KEY="你的上游密钥" \
uv run python -m src.main
```

方式二：先激活 `.venv`，再启动：

```bash
source .venv/bin/activate
CLAUDE_BASE_URL="支持claude response协议的baseurl" \
ANTHROPIC_API_KEY="你的上游密钥" \
python -m src.main
```

默认监听地址是：

```text
http://127.0.0.1:7072
```

## 调用示例

```bash
curl http://127.0.0.1:7072/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "Auto",
    "messages": [
      {"role": "user", "content": "请只回复两个字：你好"}
    ],
    "max_tokens": 32,
    "temperature": 0
  }'
```

流式调用：

```bash
curl -N http://127.0.0.1:7072/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "360-glm-5.2",
    "messages": [
      {"role": "user", "content": "请用一句话回答：1+1等于几？"}
    ],
    "max_tokens": 64,
    "stream": true
  }'
```

如果设置了 `PROXY_API_KEY`，客户端需要添加任一请求头：

```bash
-H 'Authorization: Bearer 你的代理密钥'
```

或：

```bash
-H 'x-api-key: 你的代理密钥'
```

## 健康检查

```bash
curl http://127.0.0.1:7072/health
```

## 测试

使用 `uv`：

```bash
uv sync --dev
uv run python -m pytest
uv run python -m compileall src tests
```

或在已激活 `.venv` 后运行：

```bash
python -m pytest
python -m compileall src tests
```

## 常见问题

### `ModuleNotFoundError: No module named 'uvicorn'`

说明当前 `python` 没有使用项目虚拟环境。优先使用下面的命令启动：

```bash
CLAUDE_BASE_URL="支持claude response协议的baseurl" \
ANTHROPIC_API_KEY="你的上游密钥" \
uv run python -m src.main
```

或者先激活虚拟环境：

```bash
source .venv/bin/activate
python -m src.main
```
