# OpenAI Responses 透明代理实施规划

状态：已获用户整体批准，待实施

目标分支：`openai-responses`

分支起点：`493b6df`

## 1. 最终结果

该分支不再承担 OpenAI Chat Completions 与 Claude Messages 的双向协议转换。

它只提供一个 OpenAI Responses 入口，并在请求上游前完成代理鉴权、智企套餐选择、普通密钥回退和必要 Header 注入。

```text
调用方
  → POST /v1/responses（原始 OpenAI Responses body）
  → 本代理校验自身访问密钥
  → 本代理只解析顶层 model 和 stream
  → 查询智企目录并解析请求级 route
      ├─ 模型命中且支持 responses：使用套餐 key 和套餐 Header
      └─ 模型完全未命中：使用 .env 普通 key
  → POST ${CLAUDE_BASE_URL}/v1/responses（原始 body bytes）
  → 原样返回上游状态、过滤后的端到端响应 Header 和 body bytes
```

当前 `.env`：

```dotenv
CLAUDE_BASE_URL="https://code.jizhi.360.cn/aiproxy"
```

当前配置解析后的最终上游地址应为：

```text
https://code.jizhi.360.cn/aiproxy/v1/responses
```

该 host 不写死在代码中。`CLAUDE_BASE_URL` 仍可配置，但它必须表示 `/v1` 之前的服务根地址；客户端只负责在去掉末尾 `/` 后追加一次 `/v1/responses`。

## 2. 已确认事实

- 当前 `openai-responses` 分支仍是 `/v1/chat/completions → /v1/messages` 转换器，尚未修改业务代码。
- 当前基线为 `58 passed`，`bash tests/test_start_sh.sh` 通过。
- 已对图片中的 11 个模型逐一执行真实非流式 Responses 请求：8 个成功，3 个 WisGPT 模型因额度耗尽返回 HTTP 402。
- 当前智企目录精确命中 7 个图片模型，这 7 个模型都声明支持 `responses`，并且套餐请求全部成功。
- 未命中目录的 `360-Wiscode-Multimodal` 使用 `.env` 普通 key 回退成功。
- 已执行真实流式套餐请求：上游返回 HTTP 200、`text/event-stream`，以 `response.completed` 结束，不包含 Chat Completions 的 `[DONE]`。
- OpenAI 官方文档确认同一 `/responses` 入口同时承载非流式对象与 Responses 原生事件流。
- HTTPX 官方文档提供不做内容解码的 `aiter_raw()`，适合原始 SSE 字节转发；手动流模式必须显式关闭响应。

## 3. 本次目标

- 公开提供 `POST /v1/responses`。
- 请求 body 不做字段转换、删减、补默认值或重新序列化。
- 非流式响应不解析、不重建 Responses 对象。
- 流式响应不解析 SSE、不改写事件、不补 `[DONE]`。
- 保留代理客户端鉴权 `PROXY_API_KEY`。
- 保留 `X-Src: ide` 和 task/trace 上下文行为。
- 保留完整智企目录校验、缓存、single-flight 和套餐选择规则。
- 模型完全未命中智企目录时，使用 `.env` 普通 key 回退。
- 保留根目录 `start.sh`，继续支持一条命令准备环境并启动。
- 不新增 Python 依赖。

## 4. 非目标

- 不支持 `/v1/chat/completions`。
- 只支持 Responses 的创建方法 `POST /v1/responses`；不支持 Retrieve、Cancel、Delete、Input Items、Compact 等其他 Responses resource 方法。
- 不把 Responses 转换成 Claude Messages。
- 不把 Claude Messages 转换成 Responses。
- 不实现 Responses 完整 schema 的本地镜像。
- 不修改 aiproxy、WisCode、CC Switch 或其他仓库。
- 不增加模型别名映射。
- 不在目录失败时静默绕过套餐策略。
- 不保证 HTTP 传输 chunk 的切分位置与上游一致；只保证拼接后的业务字节一致。
- 不把任意调用方 Header 无条件复制给上游。
- 不 push 分支，除非用户后续明确要求。

## 5. 审批即采用的设计决策

用户不希望逐项拷问。批准本规划即表示接受以下默认决策。

### 5.1 旧接口与旧代码

- 该分支移除 `/v1/chat/completions`，并通过测试固定返回 404。
- 删除失去调用者的 Chat Completions ↔ Claude Messages 转换实现及其专属测试。
- 删除 Claude 特有的 `anthropic-version`、stop reason 映射、tool/image/thinking 转换和流式 `[DONE]` 合成。
- 主分支仍保留旧产品，因此新分支不为旧协议承担兼容负担。

### 5.2 请求透明边界

- Endpoint 先读取原始请求 body bytes。
- 代理另行解析一份 JSON，只提取自身路由所必需的顶层 `model` 和 `stream`。
- 发往上游时使用 `content=raw_body`，禁止使用 `json=payload` 重新序列化。
- 未知字段、字段顺序、Unicode、空白、嵌套 `input`、tools、reasoning 和未来字段均不被代理修改。

本地只验证：

- body 必须是合法 JSON object。
- 顶层 `model` 和 `stream` 均不得重复出现；即使重复值相同也返回 400，避免代理与上游对重复键采用不同解释后选错鉴权。
- `model` 必须是去除首尾空白后仍非空的字符串；代理不裁剪、不改写它，目录匹配和上游 body 都使用原值。
- `stream` 缺失时按 `false`；存在时必须是 boolean。

其余 Responses 参数是否合法由上游判断。

首版会将单个请求 body 读入内存一次，并另行解析一份 JSON 用于路由。首版不新增代理自定义 body 大小限制；部署层和上游已有的请求大小限制仍然生效。

### 5.3 请求 Header 边界

请求 Header 采用受控构造，不做全量透明转发。

始终由代理产生或选择：

- `Content-Type: application/json`
- `Authorization: Bearer <selected-key>`
- `x-api-key: <selected-key>`
- `x-src: ide`
- `x-request-id`
- `x-client-task-id`
- `x-client-trace-id`

压缩协商采用一条显式规则：

- 调用方提供非空 `Accept-Encoding` 时，代理把该字段加入受控允许集合并原值转发。
- 调用方缺失该字段或值为空时，代理显式发送 `Accept-Encoding: identity`，覆盖 HTTPX 自动添加的 `gzip, deflate`。
- 若调用方重复发送 `Accept-Encoding`，代理按该 Header 的列表语义用逗号连接非空值后转发；不把它当作多个独立鉴权或路由指令。

命中智企套餐时额外加入：

- `X-Ai-Forward-Url: https://llm.api.zyuncs.com/v1`
- `X-Pkg-Model`
- 合法邮箱存在时的 `X-Ai-Forward-Email`

不得从调用方复制：

- `Authorization`
- `x-api-key`
- `Host`
- `Content-Length`
- `Cookie`
- `Proxy-Authorization`
- `Connection` 及其声明的逐跳 Header
- 其他未经批准的调用方 Header

这意味着“透明”首先指请求和响应协议内容不转换，而不是放弃代理的鉴权隔离职责。

首版因此不会转发 `Idempotency-Key`、`OpenAI-Beta` 或其他未列出的语义 Header。需要这些 Header 的客户端必须等后续按名称扩充允许集合；本规划不以未知 Header 透传换取兼容性。

### 5.4 智企套餐与普通密钥回退

```text
目录成功，模型完全未出现
  → 使用 .env 普通 key

目录成功，模型出现，但没有 responses 能力
  → HTTP 400，不回退

目录成功，模型出现、支持 responses、存在可用套餐
  → 使用套餐 key 和套餐 Header

目录成功，模型出现、支持 responses、套餐全部不可用
  → HTTP 503，不回退

auth/目录请求/目录契约失败
  → 返回现有 401/500/502，不回退
```

套餐选择继续遵循：

1. `models[].name` 区分大小写 exact match。
2. 只在 `apiNames` 包含区分大小写的精确字符串 `responses` 的候选中选择；`Responses` 或其他大小写不匹配的值均不算支持。
3. `zyzj_package` 优先。
4. `sfdj_package` 其次。
5. 未知非空 identifier 最后。
6. 同组按目录返回顺序选择第一个可用项。
7. 过期、`exhausted=true`、`enabled=false` 的项不可用。
8. `expireAt` 只用于过滤，不参与排序。

普通密钥继续使用兼容优先级：

```text
CLAUDE_API_KEY
  → ANTHROPIC_API_KEY
```

模型未命中且普通 key 也未配置时，本地返回 HTTP 500 配置错误，不发送无鉴权上游请求。

### 5.5 上游响应边界

代理不解释任何已经完整取得的上游 HTTP 响应的业务含义。

以下内容保持：

- HTTP status。
- 拼接后的 raw body bytes。
- 除下方过滤项以外的全部端到端响应 Header，包括未知 Header、`Content-Type`、`Content-Encoding`、`Cache-Control`、`Retry-After`、`WWW-Authenticate`、请求追踪和限流 Header。
- 重复 Header 的独立字段值，包括多个 `Set-Cookie`；不得用逗号合并。

以下内容剔除或由下游连接重新计算：

- `Connection`，以及其所有字段值中以逗号声明的动态逐跳 Header；字段名匹配不区分大小写。
- `Keep-Alive`。
- `Proxy-Authenticate`、`Proxy-Authorization`。
- `TE`、`Trailer`、`Transfer-Encoding`、`Upgrade`。
- `Content-Length`。
- `Server`、`Date`；由当前下游服务器决定是否生成。

实现通过 ASGI `raw_headers` 传递过滤后的 `(name_bytes, value_bytes)` 列表，保持字段顺序和重复项；不使用会折叠同名字段的普通 mapping。HTTP chunk 边界仍不属于保真范围。

流式和非流式都通过 HTTPX `send(..., stream=True)` 打开上游响应，并且都从 `aiter_raw()` 读取未经内容解码的 bytes。非流式仅多一步：在下游响应开始前聚合全部 raw chunks。这样 `Content-Encoding: gzip` 与压缩 body 始终成对保留，不会出现已解压 body 搭配 gzip Header。

因此下面这些情况也必须原样返回，而不是改成 FastAPI `detail`：

- HTTP 200 的非 JSON body。
- HTTP 402/429/500 的 JSON body。
- HTTP 4xx/5xx 的纯文本 body。
- 上游 `Retry-After` 和限流 Header。

### 5.6 代理自身错误

代理是否还能生成自己的 HTTP 错误，以“下游响应是否已经开始”为边界：

- 下游响应开始前，连接失败或 body 读取中断返回 502，超时返回 504。
- 下游响应开始后，状态码和 Header 已无法改写；代理只终止 body、清理资源并记录脱敏日志。
- 已完整取得上游响应后，代理原样返回上游 status、过滤后的 Header 和 raw body。

本地错误一律使用现有 FastAPI `{"detail": "..."}` 形态，并保持信息脱敏。具体来源包括：

- 代理访问密钥错误：401。
- 请求不是 JSON object、缺少合法 model、stream 类型错误：400。
- WisCode auth 文件或本地配置错误：沿用明确的 401/500。
- 目录网络、JSON 或契约错误：502。
- 模型存在但不支持 responses：400。
- 套餐存在但全部不可用：503。
- 连接上游失败：502。
- 上游连接或读取响应头超时：504。
- 非流式 body 完整聚合前连接中断：502；读取超时：504。

这些代理错误不伪装成上游响应。

### 5.7 流式与取消

- `stream=true` 时使用 HTTPX raw-byte iterator。
- 保留 SSE 空行、注释、多行 data、事件名、未知未来事件和上游终止事件。
- 不新增 `[DONE]`。
- 不制造 `response.completed`、`response.failed` 或代理自定义 Responses 事件。
- 下游响应开始后发生超时或连接错误时，只结束下游流、关闭资源并记录脱敏日志；HTTP 状态已经不能改写。
- ASGI 请求任务被取消时，取消必须向当前上游连接、响应头等待或 body 读取操作传播。
- 下游流发送失败或 Starlette 的断连监视触发取消时，必须停止上游读取。
- 上游 response、HTTP client 和活动请求记录必须最终清理，每项资源最多关闭一次。

首版不额外启动一个贯穿非流式请求全生命周期的 `request.is_disconnected()` 并发监听任务。因此，如果 ASGI 服务器没有取消正在等待的非流式请求任务，代理可能继续等待到既定上游超时；主动全阶段断连监控属于后续可靠性增强，不进入本次极简范围。

### 5.8 配置与启动

- 根目录 `start.sh` 必须保留。
- `start.sh` 继续从任意工作目录定位仓库。
- `.venv` 缺失或不完整时继续创建/修复并执行 `uv sync --active`。
- 最终继续 `exec python -m src.main "$@"`，完整传递参数。
- `.env` 仍由 `python-dotenv` 加载；`start.sh` 不 source `.env`。
- 外部配置名继续兼容 `CLAUDE_BASE_URL`、`CLAUDE_API_KEY`、`ANTHROPIC_API_KEY` 和 `PROXY_API_KEY`。
- 内部属性和类名改为 `upstream_*` / `Responses*`，不继续传播 Claude 语义。
- 删除 `ANTHROPIC_VERSION` 运行配置和文档。
- `CLAUDE_BASE_URL` 不再默认指向 Anthropic，且首版要求显式配置。
- `CLAUDE_BASE_URL` 必须是带 host 的绝对 `http://` 或 `https://` URL，不得带 query、fragment，不得已经以 `/v1` 或 `/v1/responses` 结尾；缺失或非法时在发送目录或模型上游请求前返回脱敏的 HTTP 500 配置错误。
- 对当前值 `https://code.jizhi.360.cn/aiproxy`，唯一拼接结果是 `https://code.jizhi.360.cn/aiproxy/v1/responses`。
- 默认监听端口统一为 7072；修正 `.env.example` 中现有的 8000 错误。
- README 把 `./start.sh` 作为首选启动方式，并说明修改 `.env` 后需要重启进程。
- FastAPI 标题和 CLI help 改为 `OpenAI Responses Proxy`；版本继续为 `1.0.0`。
- 根路径 JSON 只公布 `responses: /v1/responses` 与 `health: /health`。
- `/health` 保留 `status` 与 `timestamp`，将旧 Claude 字段替换为 `upstream_base_url`、`fallback_api_key_configured`、`client_api_key_validation`；它只表示进程和本地配置状态。
- `pyproject.toml` 的 distribution 与 console script 都改名为 `openai-responses-proxy`，同步更新 `uv.lock`；旧 `claude-openai-proxy` console script 不在该分支保留。

## 6. Module 与 Interface 设计

### 6.1 Endpoint module

位置：`src/api/endpoints.py`

Interface：公开的 `POST /v1/responses`、`/health` 和 `/`。

Implementation 只负责编排：

1. 校验代理客户端 key。
2. 读取原始 body。
3. 调用最小信封解析函数。
4. 解析一次 task/trace context。
5. 调用 route resolver。
6. 调用 upstream client。
7. 根据 stream 选择下游 Response/StreamingResponse。

Endpoint 不理解 Responses output、SSE event、tool 或 reasoning 内容。

### 6.2 Responses envelope module

建议位置：`src/models/responses.py`

Interface：

```text
parse_responses_envelope(raw_body) → {model, stream}
```

Implementation 使用能够保留 object pairs 的标准库 JSON 解码，只检查顶层 object 和顶层 `model`/`stream`，从而拒绝重复路由字段；不返回重建后的完整 body。

### 6.3 ZQI route module

位置：`src/core/zqi_catalog.py`

最终 Interface 保持小而稳定的 `resolve(model) → ZqiRoute`。

Implementation 复用现有 auth、目录、缓存、single-flight、严格 schema 和套餐优先级。expand 阶段让 resolver 接受内部协议参数，使旧入口暂用 `messages`、新入口使用常量 `responses`；contract 阶段删除旧入口后，将公开 interface 收窄回只解析 Responses。

### 6.4 Responses upstream module

位置：先在 `src/core/client.py` 中新增协议中性的 `ResponsesUpstreamClient` 与 `OpenedUpstreamResponse`，暂时保留旧 `ClaudeClient`，确保 expand 阶段旧测试继续为绿；最终 contract issue 再删除旧类。

小 Interface 隐藏：

- `/v1/responses` URL 拼接。
- 普通/套餐 key 选择。
- Header 构造与响应 Header 过滤。
- 原始 body 发送。
- 统一以 `send(..., stream=True)` 打开响应并使用 `aiter_raw()`。
- 非流式 raw chunks 聚合。
- 流式 raw iterator。
- 超时、取消和幂等清理。

HTTPX transport 保持为 internal seam。Production 使用默认 HTTP transport，测试使用 `MockTransport`；不再额外发明测试专用 port。

### 6.5 Constants module

位置：`src/core/constants.py`

所有新增固定值集中在这里，包括：

- Responses 路径与协议名。
- 上游和上下文 Header 名。
- hop-by-hop/响应过滤 Header 集合。
- 本地稳定错误消息或错误码。

每个新增函数只处理一个目标；请求信封解析、请求 Header 构造、响应 Header 过滤、流资源关闭和日志脱敏分别实现，不堆入单个函数。

## 7. 文件级变更

| 文件 | 规划动作 |
|------|----------|
| `src/api/endpoints.py` | 改成 Responses 唯一推理入口和透明响应编排 |
| `src/core/client.py` | 改成 Responses 上游 raw-byte client |
| `src/core/zqi_catalog.py` | 协议门禁从 `messages` 改成 `responses`，其余选择契约保持 |
| `src/core/constants.py` | 删除旧转换常量，增加 Responses/Header 常量 |
| `src/core/config.py` | 移除 Anthropic version，内部改用中性属性，保留外部 env 兼容 |
| `src/core/request_context.py` | 保留现有行为 |
| `src/models/responses.py` | 新增最小请求信封解析 |
| `src/main.py`、`src/__init__.py` | 更新产品名和 help 文案 |
| `start.sh` | 保留核心逻辑和可执行入口 |
| `scripts/copy-zqi-model-packages.sh` | 保留 |
| `README.md`、`.env.example`、`pyproject.toml`、`uv.lock` | 改成 Responses 透明代理说明与包名 |
| `tests/test_api.py` | 重写为公开 Responses interface 集成测试 |
| `tests/test_client.py` | 重写为 raw response、流和资源生命周期测试 |
| `tests/test_zqi_catalog.py` | 参数化并改成 responses 协议选择测试 |
| `tests/test_start_sh.sh` | 保留并扩充 start/help/参数传递门禁 |

确认无调用者后删除：

- 整个 `src/conversion/` package（含 `__init__.py`、request/response converter）
- `src/models/openai.py`
- `src/models/claude.py`
- `src/api/stream_errors.py`
- `tests/test_conversion.py`
- `tests/test_request_converter.py`
- `tests/test_stream_errors.py`

删除这些文件不会删除仍有用途的解释性注释；其对应的协议转换能力已经明确退出该分支。

## 8. Test seams 与验收矩阵

最高层行为使用两个互补 seam，不把它们混写成同一种能力：

```text
TestClient + httpx.MockTransport
  → POST /v1/responses
  → 验证普通请求、代理鉴权、路由、目标 URL、原始请求 body 与受控请求 Header

直接 ASGI receive/send driver + gated AsyncByteStream
  → 捕获未被测试客户端自动解压的 raw body/raw_headers
  → 注入 http.disconnect 或直接取消 ASGI task
  → 验证 gzip、重复 Header、流式字节、取消传播与幂等关闭
```

主要验收：

| 场景 | 必须观察的结果 |
|------|----------------|
| 普通 key 非流式 | URL 正确；请求 bytes 不变；使用 `.env` key；状态、body、允许 Header 不变 |
| 套餐 key 非流式 | 套餐 key 覆盖普通 key；智企 Header 完整；调用方 auth 不泄漏 |
| 未知 Responses 字段 | 不丢失、不重排、不重建 |
| Responses 协议选择 | `responses` 可用；仅 `messages` 返回 400；完全未命中才回退 |
| 套餐状态 | 过期、耗尽、禁用跳过；按内网→外网→未知选择；全部不可用返回 503 |
| 非流式上游错误 | 402/429/500 的 status、JSON/文本 bytes、Retry-After 原样返回 |
| HTTP 200 非 JSON | 仍返回 200 和原始 body，不改成 502 |
| 非流式 gzip | raw 压缩 bytes 与 `Content-Encoding: gzip` 成对保留，重复 Header 不折叠 |
| 调用方未声明压缩 | 上游请求显式带 `Accept-Encoding: identity`，不继承 HTTPX 的 gzip 默认值 |
| 调用方声明 gzip | 上游收到原 `Accept-Encoding` 语义，下游收到 gzip raw bytes 与匹配的 Content-Encoding |
| 非流式读取中断 | 下游尚未开始时，连接中断返回 502、读取超时返回 504，且资源关闭一次 |
| 流式成功 | 拼接 bytes 相同；空行/注释/未知事件保留；不补 `[DONE]` |
| 流建立前 4xx/5xx | 下游先收到相同 status、body 和允许 Header |
| 流中断 | 已发送 bytes 不变；不合成事件；资源只关闭一次 |
| 连接失败/超时 | 分别返回代理 502/504；不泄露 key；无活动请求残留 |
| 取消与断开 | 不释放 gated upstream 也能观察到 ASGI task 取消向上传播；流式 `http.disconnect`/发送失败会停止读取并清理 |
| 代理访问鉴权 | Bearer/x-api-key 正确值可用；错误值 401；失败时不查目录、不请求模型 |
| 旧接口 | `/v1/chat/completions` 返回 404 |
| `start.sh` | 缺/坏 `.venv` 可恢复；已有环境不重复同步；参数原样传递；外部 cwd 可启动 |

## 9. 拟生成的 Vertical Issues

审批后不再询问拆分方式，直接生成以下 issues。

### Issue 01：expand——新增 Responses 非流式透明入口

在暂时保留旧 Chat/Claude 链路的前提下，新增 Responses 专用且显式必填的 Base URL 配置、`ResponsesUpstreamClient`、最小信封解析和 `/v1/responses` 非流式纵切片。交付原始 body 发送、普通 key 回退、代理鉴权、上下文 Header、raw status/body/header 返回、gzip/重复 Header，以及本地 400/500/502/504。旧 Claude 配置属性在 expand 阶段暂时保留，旧测试与新测试在本 issue 结束时同时通过。

受阻于：无。

### Issue 02：智企 Responses 套餐路由接入

以 expand 兼容方式让 route resolver 按调用入口选择内部协议；旧入口暂用 `messages`，新入口使用区分大小写的 `responses`。从公开入口验证套餐 key/Header、内外网优先级、状态过滤、严格失败与“完全未命中才使用普通 key”。

受阻于：Issue 01 的 Responses 上游 interface。

### Issue 03：Responses 原生流式透传与取消清理

实现 `aiter_raw()` SSE 透传、raw headers、流前错误、流中断、ASGI task 取消传播、流式断连/发送失败清理和幂等资源释放。

受阻于：Issue 02。该 issue 与 Issue 02 会修改相同 endpoint、client 和 API 测试，明确串行执行。

### Issue 04：contract——删除旧 Chat/Claude 产品路径

在 Responses 非流式和流式测试已经覆盖后，删除 `/v1/chat/completions`、旧 `ClaudeClient`、转换 package、旧模型、旧 SSE 错误生成器及其专属测试。收窄 resolver interface，只保留 Responses 协议。用公开端点测试固定旧接口 404；提交结束时全套测试保持为绿。

受阻于：Issue 03。

### Issue 05：`start.sh`、配置和用户可见文档收口

保留并扩充 `start.sh` 门禁，清理 contract 后残留的旧配置属性，并更新 FastAPI 标题、根路径、健康检查、CLI help、README、`.env.example`、`pyproject.toml` 与 `uv.lock`。把首选启动方式固定为 `./start.sh`，把 package/console script 改名为 `openai-responses-proxy`。

受阻于：Issue 04。

### Issue 06：最终集成与真实上游闭环

运行完整静态检查、自动测试、本地服务测试和敏感信息扫描。所有 11 个图片模型都必须通过本地 `http://127.0.0.1:7072/v1/responses` 重测。随后必须使用用户指定的 `z-ai/glm-5.3-flash` 真实覆盖字符串 input、四种 message role、两种 assistant phase、`input_text`、自包含 `input_image`、自包含 `input_file`、function call/function call output 两阶段交互和原生流式；任一项失败都继续修正并重跑整组。只提交必要的验证修正与脱敏结果记录。

受阻于：Issue 05。

## 10. 最终验证门禁

自动验证：

```text
uv run python -m pytest
uv run python -m compileall src tests
bash -n start.sh tests/test_start_sh.sh
bash tests/test_start_sh.sh
./start.sh --help
```

本地服务验证：

- `./start.sh` 能启动并监听 7072。
- `/health` 只能证明进程存活，不能代替真实请求。
- 通过本地代理验证一个普通 key 非流式请求。
- 通过本地代理验证一个套餐 key 非流式请求。
- 通过本地代理验证普通 key 与套餐 key 的原生流式请求。
- 验证旧 `/v1/chat/completions` 为 404。

真实上游闭环：

- 服务在本地启动后，所有请求都先进入 `http://127.0.0.1:7072/v1/responses`，不再用直连上游代替代理验收。
- 通过本地代理重新执行图片中 11 个模型的最小非流式请求，记录 HTTP、Responses status、error 和文本。
- 通过本地代理至少对一个套餐模型和一个 `.env` 回退模型执行 `stream=true`。
- 使用 `z-ai/glm-5.3-flash` 通过本地代理执行正式 spec 中定义的完整 Responses 消息矩阵；每项都必须是 2xx、`status=completed`、`error=null` 并具有可验证输出。
- 完成一次真实 function call → function_call_output 续接和一次以 `response.completed` 结束的原生流。
- 3 个 WisGPT 模型当前额度耗尽不作为实现失败；只要状态和 body 由代理原样返回即满足透明契约。
- 不输出或保存真实 access token、普通 key、套餐 key 或完整敏感 Header。

代码审查门禁：

- 检查最终 diff，确认没有保留可达的 Chat/Claude 转换路径。
- 检查所有新增固定值均位于常量文件。
- 检查新增函数职责单一，共享逻辑没有复制到流式/非流式两条路径。
- 检查所有新增代码都包含解释职责、边界和失败清理原因的详细中文注释，且未误删仍有用途的原有注释。
- 检查日志不包含请求 body、响应 body、Authorization、x-api-key 或 token。
- 检查无新增依赖、无主工作树改动、无遗留软链接。

## 11. Blocking edges 与未验证边界

真实 blocking edges：

- 智企目录验证依赖本机 `~/.wiscode/auth.json` 和可用网络。
- 普通回退验证依赖本地忽略的 `.env` key。
- 某个真实模型可能因额度、限流或临时上游故障失败；必须按状态/body 判断，不能把它直接归因于代理。

已经在规划层固定、仍需红灯测试证明的边界：

- 非流式和流式都必须使用 `send(..., stream=True)` 与 `aiter_raw()`；gzip body 和 `Content-Encoding` 必须成对保真。
- `Accept-Encoding` 必须按调用方能力协商；缺失或空值时显式使用 `identity`，不能让 HTTPX 代替调用方声明 gzip 能力。
- 重复响应 Header 必须通过 ASGI `raw_headers` 保留；`Connection` 声明的动态逐跳字段必须过滤。
- ASGI task 取消必须向上游传播，流式断连或发送失败必须清理资源；首版不承诺独立的非流式全阶段断连监听器。

这些不是留给实现者决定的开放项。它们会写成明确 scenario，由 Issue 01 和 Issue 03 的红灯测试验证。

## 12. 审批后的自动动作

收到整体批准后，直接执行以下动作，不再单独询问：

1. 完整读取 `grill-with-docs` 的 spec 模板。
2. 将本规划固化为带稳定 `REQ-*` 与 Given/When/Then `SCN-*` 的 `.scratch/openai-responses-proxy/spec.md`。
3. 完整读取 tickets 模板。
4. 生成 `.scratch/openai-responses-proxy/issues/01-*.md` 至 `06-*.md`。
5. 自审 requirement、scenario、错误、兼容性、test seam 与 issue 覆盖关系。
6. 提交 findings、plan、spec 和 issues，记录 Review Base Commit。
7. 把 Review Base Commit 写回 findings，并单独提交 metadata 更新。
8. 按技能流程交给 implementation 阶段；默认不 push。
