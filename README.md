# OpenAI Responses Proxy

这是一个只代理 OpenAI Responses Create 请求的轻量服务。

客户端把原生 `POST /v1/responses` 请求交给本服务。服务只处理代理鉴权、智企套餐选择、普通密钥回退、必要请求头和连接清理。请求 JSON、非流式响应 body 和流式 SSE 都不做协议转换。

“透明”只表示协议内容不转换。调用方的 `Authorization`、`x-api-key`、`Host`、`Content-Length` 和逐跳 Header 不会直接进入上游；上游身份由代理根据智企目录或本地配置决定。

## 请求如何选择密钥

服务先读取 `~/.wiscode/auth.json` 并查询智企套餐目录，然后按请求中的原始 `model` 精确匹配：

1. 目录中存在支持 `responses` 的可用套餐时，使用套餐 key，并携带智企转发 Header。
2. 目录成功返回，但其中完全未出现该模型时，使用 `.env` 的普通密钥。
3. 模型已经出现但不支持 `responses` 时，返回 400，不使用普通密钥绕过目录限制。
4. 支持 `responses` 的套餐全部过期、耗尽或禁用时，返回 503，不回退。
5. WisCode 认证、目录网络、JSON 或结构校验失败时，返回明确错误，不回退。

普通密钥优先读取 `CLAUDE_API_KEY`，没有时兼容读取 `ANTHROPIC_API_KEY`。这两个名字只为兼容现有本地配置，不代表请求会被转换为 Anthropic 协议。

## 配置

复制示例文件：

```bash
cp .env.example .env
```

至少需要设置服务根地址：

```dotenv
CLAUDE_BASE_URL="https://code.jizhi.360.cn/aiproxy"
```

这个值必须是 `/v1` 之前的 HTTP(S) 根地址。代理会唯一拼接成 `https://code.jizhi.360.cn/aiproxy/v1/responses`。

如果需要调用智企目录中完全未出现的模型，再配置普通回退密钥：

```dotenv
CLAUDE_API_KEY="你的普通上游密钥"
# ANTHROPIC_API_KEY="兼容的普通上游密钥"
```

可选配置：

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `PROXY_API_KEY` | 空 | 设置后，客户端必须提供同值 Bearer 或 `x-api-key` |
| `HOST` | `0.0.0.0` | 本地监听地址 |
| `PORT` | `7072` | 本地监听端口 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `REQUEST_TIMEOUT` | `90` | 上游连接、写入和连接池超时秒数 |
| `READ_TIMEOUT` | `480` | 上游响应读取超时秒数 |

`.env` 由 Python 的 `python-dotenv` 加载，`start.sh` 不会把它当作 Shell 脚本执行。修改 `.env` 后需要重启服务才能生效。

## 启动

首选根目录脚本：

```bash
./start.sh
```

脚本可以从任意工作目录调用。它会定位仓库自身，在 `.venv` 缺失或不完整时创建或修复环境并同步依赖；完整环境不会重复同步。传给脚本的参数会原样交给应用。

查看帮助：

```bash
./start.sh --help
```

也可以手工使用 `uv`：

```bash
uv sync --dev
uv run python -m src.main
```

默认服务地址为 `http://127.0.0.1:7072`。

## 非流式调用

未设置 `PROXY_API_KEY` 时：

```bash
curl 'http://127.0.0.1:7072/v1/responses' \
  -H 'Content-Type: application/json' \
  --data-binary '{
    "model": "z-ai/glm-5.3-flash",
    "input": "只回复 TEST OK"
  }'
```

设置了 `PROXY_API_KEY` 时，在请求中增加任意一种代理凭据：

```bash
-H "Authorization: Bearer $PROXY_API_KEY"
```

或：

```bash
-H "x-api-key: $PROXY_API_KEY"
```

## 流式调用

```bash
curl -N 'http://127.0.0.1:7072/v1/responses' \
  -H 'Content-Type: application/json' \
  --data-binary '{
    "model": "z-ai/glm-5.3-flash",
    "input": "只回复 TEST OK",
    "stream": true
  }'
```

代理按原始字节转发上游 SSE，包括上游实际产生的未知事件和终止事件。代理不会追加 `[DONE]`，也不会合成 `response.completed` 或错误事件。

## 健康检查

```bash
curl 'http://127.0.0.1:7072/health'
```

健康检查只说明进程存活和本地配置状态。它不会请求智企目录或模型，因此不能证明真实上游可用。

## 测试

```bash
uv sync --dev
uv run python -m pytest -q
bash tests/test_start_sh.sh
uv run python -m compileall -q src scripts tests
uv lock --check
```

服务已经由 `start.sh` 启动后，可以通过本地代理复跑真实验收：

```bash
uv run python -m scripts.verify_live_responses --mode all
```

`models` 模式逐项记录图片中的 11 个模型；`messages` 模式使用 `z-ai/glm-5.3-flash` 验证字符串输入、四种角色、两种 assistant phase、三种 content、function call 两阶段和原生流式响应。工具只请求本地代理，仅读取可选的 `PROXY_API_KEY`，不会读取或输出上游密钥。
