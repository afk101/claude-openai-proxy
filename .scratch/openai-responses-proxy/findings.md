# Findings & Decisions
<!--
  WHAT：openai-responses-proxy 任务的持续证据与决策记录。
  WHY：实现前先固定协议边界、套餐路由、透明转发与测试契约。
  WHEN：每两次重要读取、搜索、实验或浏览后追加更新。
-->

## Requirements

- 在 `openai-responses` 分支实现 OpenAI Responses 透明代理。
- 对外入口改为 `POST /v1/responses`，请求体已经是 OpenAI Responses 格式。
- 请求和响应不再转换为 Claude Messages 或 Chat Completions。
- 代理只承担客户端鉴权、智企套餐目录匹配、套餐选择、上游鉴权与必要请求头注入。
- 模型未命中智企目录时，继续使用 `.env` 中的普通上游密钥回退。
- 上游基础地址继续读取 `CLAUDE_BASE_URL`；当前目标值为 `https://code.jizhi.360.cn/aiproxy`。
- 必须保留可直接启动服务的 `start.sh`。
- `.env` 中的真实密钥不得写入源码、日志、测试快照、findings、spec、issues 或提交记录。
- 用户要求本轮不进行逐题拷问；先提交完整规划供整体审批，审批后直接生成 spec 与 vertical issues。
- 用户已批准规划，并要求随后直接使用 `$implement` 执行到完成。
- 最终完成条件新增：必须用 `z-ai/glm-5.3-flash` 通过本地代理真实模拟全部 OpenAI Responses 消息类型，任一失败都继续修正并重跑直到通过。

## Findings

- 当前分支为 `openai-responses`，起点为 `493b6df`；创建分支后尚未修改业务代码。
- 本轮之前已对 `https://code.jizhi.360.cn/aiproxy/v1/responses` 完成 11 个图片模型的真实非流式请求。
- 真实请求中 8 个模型返回 HTTP 200、Responses `status=completed`；3 个 `WisGPT-5.6-*` 返回 HTTP 402“WisGPT 模型额度已用尽”。
- 当前智企目录精确命中 7 个图片模型，并且这些模型的 `apiNames` 均包含 `responses`；7 个套餐路由请求全部成功。
- `360-Wiscode-Multimodal` 未命中智企目录，但使用 `.env` 普通密钥回退后成功。
- `WisGPT-5.6-Terra`、`WisGPT-5.6-Luna`、`WisGPT-5.6-Sol` 未命中当前智企目录，使用 `.env` 回退后到达上游并被额度门禁拒绝。
- 套餐请求成功使用了现有路由语义：`Authorization`、`x-api-key`、`x-src: ide`、`X-Ai-Forward-Url`、`X-Pkg-Model`，存在邮箱时再加 `X-Ai-Forward-Email`。

逐项脱敏结果如下。验证日期均为 `2026-09-03`（Asia/Shanghai）；表中没有保存任何 access token、普通 key、套餐 key 或完整请求 Header。

| 模型 | 实际路由 | HTTP | Responses status | error | 原始文本结果 |
|------|----------|------|------------------|-------|----------|
| `WisGPT-5.6-Terra` | `.env` 普通密钥回退 | 402 | — | WisGPT 模型额度已用尽 | — |
| `WisGPT-5.6-Luna` | `.env` 普通密钥回退 | 402 | — | WisGPT 模型额度已用尽 | — |
| `WisGPT-5.6-Sol` | `.env` 普通密钥回退 | 402 | — | WisGPT 模型额度已用尽 | — |
| `deepseek/deepseek-v4-flash` | 智企套餐 | 200 | `completed` | — | `TEST OK` |
| `deepseek/deepseek-v4-pro` | 智企套餐 | 200 | `completed` | — | `TEST OK` |
| `openai/gpt-5.6-sol-cpr` | 智企套餐 | 200 | `completed` | — | `TEST OK` |
| `openai/gpt-5.6-terra-cpr` | 智企套餐 | 200 | `completed` | — | `TEST OK` |
| `openai/gpt-5.6-luna-cpr` | 智企套餐 | 200 | `completed` | — | `TEST OK` |
| `z-ai/glm-5.3-flash` | 智企套餐 | 200 | `completed` | — | `TEST OK` |
| `qwen/qwen3.8-flash` | 智企套餐 | 200 | `completed` | — | `\n\nTEST OK` |
| `360-Wiscode-Multimodal` | `.env` 普通密钥回退 | 200 | `completed` | — | `\n\nTEST OK` |

- 历史验证经验要求同时判断 HTTP 状态、Responses `status`、`error` 与实际输出文本；HTTP 200 本身不足以证明调用成功。
- OpenAI 官方 Responses API 的最小创建请求由 `POST /responses`、`model` 和 `input` 构成；本任务实测采用了这一最小形态。
- OpenAI 官方当前 `easy_input_message` schema 明确列出四种 role：`user`、`assistant`、`system`、`developer`；assistant 还可携带 `commentary` 或 `final_answer` phase。
- 同一官方 schema 将稳定 message content 列为 `input_text`、`input_image`、`input_file`，其中图片可使用 base64 data URL，文件可使用内嵌 `file_data` 与 `filename`。
- 为让“全部消息类型”成为有限且可执行的 completion gate，本任务按上述 message schema 覆盖字符串 input、四种 role、两种 assistant phase、三种 content；另补一次 function call/function call output 两阶段 agent 交互和一次原生流式请求。
- built-in tools、MCP、computer use、audio output、模型选择的 output item 和完整 SSE event union 不是 message input 类型，不纳入“全部消息类型”；代理仍须通过自动测试证明未知 item/event 的字节透明性。
- OpenAI 官方文档把非流式和流式创建请求建模为同一 Responses 入口；`stream: true` 时返回的是 Responses 原生事件流，而不是 Chat Completions chunk。
- 官方流式示例包含具名 SSE 事件 `response.created`、`response.in_progress`、`response.output_item.added`、`response.completed`；透明代理若解析或重建这些事件，会增加丢失当前及未来事件类型的风险。
- 官方非流式返回对象允许 `completed`、`failed`、`in_progress`、`cancelled`、`queued`、`incomplete` 等状态。因此代理不能把 HTTP 200 等同于业务完成，也不应改写 `status` 或 `error`。
- 当前公开推理入口在 `src/api/endpoints.py:55-101`：先把 Chat Completions 请求转换为 Claude Messages，再按 `stream` 分支调用上游并转换响应；新方案需要替换这条端到端链路，而不是在转换器中增加 Responses 分支。
- 当前端点已经具备可复用的客户端 API Key 校验、请求 ID、客户端 task/trace 上下文、断连检测与资源关闭逻辑；这些属于网关职责，不依赖 Claude 协议。
- 当前 `ClaudeClient` 同时承担 URL 构造、鉴权头、错误翻译、JSON 解析、流生命周期与请求取消。透明方案若继续沿用这一类，需要将其收窄/重命名为 Responses 上游客户端，并移除 Claude 特有的 `anthropic-version`、错误文案和 `/v1/messages`。
- 当前 `tests/test_api.py` 已使用 FastAPI `TestClient` 加 `httpx.MockTransport` 从公开端点捕获真实上游 HTTP 请求；这是可复用的最高层测试 seam，能够直接验证目标 URL、原始请求体、头部覆盖、状态码和 SSE 字节。
- 当前测试已经固定两项必须保留的安全行为：调用方鉴权不能覆盖代理控制的上游鉴权；只透传 `x-client-task-id` 与 `x-client-trace-id`，不透传任意无关请求头。
- 现有流式代码在上游响应开始后自行生成 Chat Completions 错误 SSE 和 `[DONE]`。这与 Responses 原生透明流冲突；新方案应原样传输上游已产生的字节，遇到中途网络错误只关闭流并记录脱敏日志，不能伪造另一种协议事件。
- `start.sh:1-67` 已是职责清楚的启动器：定位项目目录、必要时创建/修复 `.venv`、首次同步依赖、激活后 `exec python -m src.main "$@"`。Responses 改造不要求重写脚本，只需保留它并保持现有 shell 测试通过。
- `tests/test_start_sh.sh:26-72` 以假的 `uv` 和 Python 验证缺失虚拟环境时的完整启动链；该测试可以原样保留，并新增/更新 `--help` 输出断言即可覆盖产品命名变化。
- `Config` 当前暴露 `claude_api_key`、`claude_base_url`、`anthropic_version`。用户明确给出的 `.env` 仍使用 `CLAUDE_BASE_URL`，所以本任务应保留该环境变量兼容性，但内部属性可以改成协议中性的 upstream 命名；`ANTHROPIC_VERSION` 在 Responses 路径不再需要。
- `ZqiRouteResolver.resolve` 当前对所有请求先读取目录，模型完全未命中时返回无套餐 key 的 route；只有目录成功且未命中模型才会触发普通密钥回退。
- 模型一旦在目录出现但没有 `messages` 协议，当前实现返回 400；改造后应将协议判定改为 `responses`，避免把“目录中存在但协议不支持”错误地当成未命中并回退。
- 已命中且支持协议的候选套餐按 `zyzj_package`、`sfdj_package`、未知类型依次选择，并排除已过期、额度耗尽和模型禁用项；全部不可用时返回 503 并汇总原因。这套行为可直接复用。
- `.env.example`、README、FastAPI 标题、根端点信息、CLI `--help`、包描述和 console script 名称均仍写着 Claude/Chat Completions；正式实现必须系统更新用户可见文案，同时保留用户明确要求的环境变量和 `start.sh` 使用方式。
- 当前 Python 基线测试为 `58 passed`；唯一警告来自 FastAPI TestClient 对 `httpx` 的 StarletteDeprecationWarning，不是本任务引入的失败。
- 当前 `bash tests/test_start_sh.sh` 基线通过，证明启动脚本现有的缺失虚拟环境恢复路径可作为回归门禁。
- `.scratch/` 未被忽略，仓库已有被跟踪的 spec/issues 先例；本任务后续生成的 findings、spec 和 issues 可以按技能要求提交。
- 既有 `.scratch/zqi-package-support/spec.md` 已固定完整套餐契约：模型 exact match、合法空目录、目录字段严格校验、仅“完全未出现”回退、内网/外网/未知套餐优先级、TTL/single-flight、认证变化刷新、失败清旧缓存和敏感信息禁泄漏。新 spec 应复用这些稳定要求，只把协议能力从 `messages` 改为 `responses`，把最终上游路径从 `/v1/messages` 改为 `/v1/responses`。
- 既有套餐 spec 的 SCN-11 文案与实际实现曾存在“按 expireAt 选择”和“只按目录顺序”的矛盾；当前源码及 requirement 明确是不按 expireAt 排序，只过滤过期。新 spec 必须消除该旧文档歧义。
- 既有 `.scratch/upstream-request-context-headers/spec.md` 已把上游头部边界定义为 allow-list：固定 `X-Src: ide`，仅保留/补全 task/trace ID，不复制 inbound auth、Host、Content-Length、Cookie、连接级及无关头。新方案应延续这一安全边界。
- 上下文 header spec 已明确同一请求生命周期复用同一组解析结果，task ID 与 trace ID 独立生成，流式与非流式行为一致；这些要求可原样迁移到 Responses 路径。
- HTTPX 官方异步文档提供 `Response.aiter_raw()`，其语义是不进行内容解码地输出原始响应字节；手动 `send(..., stream=True)` 模式必须最终调用 `Response.aclose()`，否则会泄漏连接。这正好对应 Responses SSE 透明转发的实现 seam。
- Starlette 官方文档确认 `StreamingResponse` 可以直接消费异步生成器；因此无需理解或重建每一种 Responses 事件，只需把 HTTPX 原始字节迭代器包装为下游流并在 finally/background 中关闭资源。
- RFC 9110 把该服务归类为 HTTP gateway/reverse proxy；规范要求转发未知 end-to-end header，除非它被 `Connection` 声明为逐跳字段，或代理明确配置为阻止/转换。由于本代理是鉴权边界，规划会显式定义被阻止和被覆盖的敏感头，而不是无意间依赖框架默认值。
- 请求头存在透明兼容与安全隔离的真实取舍：全量 allow-list 最安全但可能遗漏未来 Responses 扩展头；默认透传未知端到端头最兼容但可能泄漏调用方环境。当前推荐仍以已验证的最小受控头集合为首期范围，并为后续扩展保留独立决策。
- `src/__init__.py` 在导入包时通过 `python-dotenv` 加载仓库 `.env`，且不会覆盖已经存在的进程环境；`start.sh` 不应自行 source `.env`，否则会产生两套解析语义并把配置文件当 shell 执行。
- 当前上游 4xx/5xx 会被解析并改写成 FastAPI `{"detail": ...}` 友好错误。新需求强调不转换上游响应，因此应用层 4xx/5xx 的状态、body 和内容类型也应原样返回；只有代理自身的目录、鉴权、网络连接和配置错误由代理生成。
- 当前 `OpenedClaudeStream.iter_lines()` 会按行解码并删除空行再补换行，这不是字节透明；新流包装必须改用 `aiter_raw()`，否则 SSE 分帧、非 UTF-8 字节或未来事件格式可能被改变。
- `stream_errors.py` 的 Chat Completions 风格错误事件及 `[DONE]` 常量不适用于 Responses 原生透明流。流开始后的本地异常只能记录脱敏日志、关闭资源并结束连接；不能声称返回了上游未发送的 Responses 事件。
- `start.sh` 只在 `.venv` 缺失或不完整时同步依赖；切换分支后新增依赖可能因已有 `.venv` 不同步而失败。当前依赖已经足够实现 Responses 透明代理，所以规划不新增依赖。
- `.env.example` 把默认端口写成 8000，但代码常量和 README 使用 7072；本任务更新文档时应顺便消除这一现存配置矛盾。
- 健康检查当前只证明进程存活与配置是否存在，不证明目录、套餐、普通回退或上游 Responses 可用；文档和验收必须把 `/health` 与真实 API 验证分开。
- 本轮新增真实流式验证：`deepseek/deepseek-v4-flash` 通过智企内网套餐请求 `${CLAUDE_BASE_URL}/v1/responses`，返回 HTTP 200、`Content-Type: text/event-stream`，约 1.34 秒完成。
- 该上游流共有 8 个 `event:` 与 8 个对应 `data:` 段，依次覆盖 `response.created`、`response.in_progress`、`response.output_item.added`、`response.content_part.added`、`response.output_text.delta`、`response.output_text.done`、`response.output_item.done`、`response.completed`。
- 实际 Responses SSE 以一次 `response.completed` 结束，没有 `data: [DONE]`。因此代理不得沿用当前 Chat Completions 逻辑补发 `[DONE]`；完整字节透传已同时得到官方文档与真实上游证据支持。
- 代码调查确认应删除或彻底断开旧转换路径：`src/conversion/*`、Claude 模型、Chat Completions 流错误合成器及其专属测试在新分支没有运行职责；保留它们会制造两个互相矛盾的产品模型。
- 建议将旧 `/v1/chat/completions` 在该分支移除，并用公开端点测试固定 404，防止部署方误以为该分支仍承担旧协议转换。
- `scripts/copy-zqi-model-packages.sh` 只复制/读取套餐目录，与 Messages/Responses 协议无关，可保留。
- 未命中目录且普通 key 缺失时，应在发起上游请求前返回可诊断的本地配置错误；不能发送无鉴权请求并把结果伪装成普通上游业务错误。
- 透明请求应先读取原始 bytes，再仅解析顶层路由信封；上游发送必须使用 `content=raw_body`，不能使用 `json=payload` 重新序列化。这样未知字段、Unicode、空白、嵌套 input、tools、reasoning 与未来字段都保持原样。
- 推荐本地只校验请求是 JSON object、`model` 是非空字符串、`stream` 若出现则为 boolean；这些字段直接决定套餐路由和响应模式。其余 Responses schema 由上游负责。
- 响应透明的可观察定义是：HTTP 状态、body 拼接字节和允许的端到端响应头保持一致；传输层 chunk 边界不要求一致，因为框架和 HTTP 版本可能重新分块。
- 这一阶段曾采用“上游已经产生 HTTP 响应就原样返回”的粗粒度口径；独立审阅后已被更精确的规则取代：只有完整取得上游 body 才原样返回，而能否生成代理错误以下游响应是否已经开始为边界。
- 响应头规划至少需要保留 `content-type`、`retry-after` 和请求追踪/限流类头，并剔除 `connection`、`transfer-encoding`、`content-length` 等由当前连接决定的字段。
- 客户端断开是当前真实覆盖缺口：流式路径只在收到下一个上游 chunk 后检查断开，上游静默时不能及时取消；非流式也只在发请求前检查一次。独立审阅后，首版契约收窄为 ASGI task 取消向上游传播，以及流式断连/发送失败清理；不承诺独立的非流式全阶段主动监听器。
- 当前 `create_message_stream` 的清理只捕获 `Exception`；在支持的 Python 版本中取消异常需要单独覆盖，否则连接建立阶段取消可能残留 HTTP client 或活动请求记录。
- 目录相关现有测试共 28 项通过；迁移时应把固定的 `apiNames=["messages"]` 辅助数据参数化，新增 responses-only、mixed-protocol 和协议不支持不回退用例。
- `codebase-design` 分析表明，最有 leverage 的 external seam 仍是公开 `POST /v1/responses`。测试应通过这一 interface 观察完整行为，而不是把 header builder、JSON 信封解析和响应过滤分别暴露成多个浅 seam。
- 上游 HTTP 属于 remote dependency，但 HTTPX 已提供 production transport 与 `MockTransport` test adapter；无需再叠加一套仅为测试存在的抽象 port。应把 transport 保持为 `ResponsesUpstreamClient` 的 internal seam。
- 推荐的 deep module 是协议中性的 `ResponsesUpstreamClient`：用很小的 interface 隐藏 URL 拼接、受控请求头、套餐/普通 key 选择、超时、原始 body 发送、响应头筛选、流资源和取消清理。
- Endpoint module 只承担 HTTP 入站编排：代理鉴权、读取原始 body、提取最小路由信封、解析请求上下文、调用 route resolver 与 upstream client、选择普通/流式下游响应。业务细节不应散落在 endpoint。
- 测试采用 replace 而不是 layer：新的公开 Responses interface 测试覆盖行为后，应删除旧转换器和旧转换单元测试，避免两套相反 contract 同时维护。
- 独立规划审阅指出：HTTPX 0.28.1 的 `Response.aread()` 会经过 `aiter_bytes()` 并自动解压 gzip；如果仍保留原 `Content-Encoding`，下游会收到损坏的“已解压 body + gzip Header”。因此非流式也必须用 `send(..., stream=True)` 和 `aiter_raw()` 聚合原始字节。
- 已取得上游响应头但非流式 body 尚未完整读取，是独立失败阶段。下游尚未开始时，读取超时应由代理返回 504，其他读取中断返回 502；只有完整取得 body 后才能承诺上游响应原样返回。
- `Response(headers=mapping)` 无法保持同名 Header 的重复项。响应过滤结果必须以 ASGI `raw_headers` 形式传递，保留多个 `Set-Cookie`、`WWW-Authenticate` 等端到端字段，并过滤静态及 `Connection` 动态声明的逐跳字段。
- `TestClient` 会缓冲流并可能自动解压响应，无法证明压缩 raw bytes，也不能可靠注入各阶段断连。测试 seam 必须拆成 `TestClient + MockTransport` 与直接 ASGI `receive/send` driver 两组。
- 为保持极简范围，取消契约收窄为 ASGI task 取消向上游传播，以及流式断连/发送失败时停止读取并清理。首版不额外实现一个贯穿非流式等待阶段的主动断连监听器。
- `CLAUDE_BASE_URL` 保留为可配置外部变量，但不再默认到 Anthropic。它必须显式表示 `/v1` 之前的绝对服务根 URL；当前配置的确定拼接结果是 `https://code.jizhi.360.cn/aiproxy/v1/responses`。
- 实施必须采用 expand–contract：先并存新增 Responses client/endpoint 与旧链路，再接入路由和流式，最后删除旧 Chat/Claude 实现，保证每个 issue 提交后全套测试可运行。
- 最小信封解析必须拒绝重复的顶层 `model` 或 `stream`，防止代理按一个值选套餐 key、上游按另一个值执行请求。模型原值不裁剪、不重写，`apiNames` 的 `responses` 判断区分大小写。
- HTTPX 0.28.1 会在调用方未声明时自动添加 `Accept-Encoding: gzip, deflate`。由于代理选择保留压缩 raw body，首版必须在调用方缺失或空值时显式向上游发送 `identity`；调用方提供非空值时才按列表语义转发其能力。

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| 未命中智企目录时使用 `.env` 普通密钥回退 | 用户已明确确认，且 `360-Wiscode-Multimodal` 已真实验证成功 |
| 命中目录时只选择声明支持 `responses` 的套餐 | 防止把 Responses 请求发送给只支持 `chat` 或 `messages` 的套餐 |
| 保持现有套餐优先级：内网套餐优先、外网套餐其次 | 延续仓库当前已测试的确定性选择语义 |
| 请求体不做协议转换 | 客户端与上游使用同一种 Responses 协议，转换只会扩大兼容性风险 |
| 响应模型名、状态和事件由上游决定 | 透明代理不得把上游 canonical model 名重写回客户端别名 |
| 保留 `start.sh` | 用户明确要求继续提供单命令启动入口 |
| 审批前只形成 findings 与规划，不写 spec/issues | 用户要求先审批规划；审批成功后直接生成正式文档 |
| 使用 `TestClient + MockTransport` 与直接 ASGI driver 两个互补 seam | 前者验证路由/鉴权，后者捕获压缩 raw bytes、重复 Header、断连和取消；单一 TestClient 无法证明全部契约 |
| 默认采用受控头部构造，不做全量入站头透传 | 防止客户端凭据、Host、Content-Length 和 hop-by-hop 头污染上游，同时延续现有安全边界 |
| 保留 `CLAUDE_BASE_URL` 与 `CLAUDE_API_KEY` 外部配置名，内部使用协议中性命名 | 避免用户现有 `.env` 失效，同时让新代码职责不再伪装成 Claude 客户端 |
| 目录读取或契约校验失败时不使用 `.env` 静默回退 | 无法可靠判断“未命中”时回退可能绕过套餐策略；延续当前明确失败语义 |
| 套餐过期只作为不可用过滤条件，不参与排序 | 与当前实现和既有稳定 requirement 一致，避免因到期时间改变路由优先级 |
| 完整读取的上游 HTTP 4xx/5xx 原样返回，响应完成前失败由代理按下游是否开始决定 502/504 或终止流 | 同协议网关不翻译完整上游业务响应，但不能把不完整 body 伪装成完整响应 |
| 流式与非流式都使用 `send(stream=True)` 和 `aiter_raw()` | 保持 Responses SSE 和压缩 body 原始字节，避免 `aread()` 自动解压后与 Content-Encoding 冲突 |
| 不新增 Python 依赖 | 现有 FastAPI、HTTPX、Uvicorn、python-dotenv 足够，且避免已有 `.venv` 不自动 sync 的启动风险 |
| 该分支移除 `/v1/chat/completions` 并删除无调用者的转换实现 | 分支目标是单一 Responses 网关；主分支仍保存旧产品，避免双协议职责混杂 |
| 普通回退 key 缺失时本地快速失败 | 这是代理配置缺失，不应把无鉴权流量发送给上游 |
| 本地只解析 `model` 与 `stream`，上游负责其余 schema | 代理只验证自身路由所依赖的不可约字段，避免复制完整 Responses schema |
| 响应保真以状态、允许头与拼接字节为准，不要求 HTTP chunk 边界一致 | chunk 分块属于传输实现细节，不是 Responses 业务协议 |
| ASGI task 取消向上游传播；流式断连或发送失败必须清理 | 覆盖极简版本能可靠测试的取消路径，不额外引入非流式全阶段主动监听任务 |
| HTTPX transport 作为 upstream client 的 internal seam，不新增测试专用 port | production 与 MockTransport 已构成两个 adapter，额外抽象只会扩大 interface |
| 新测试集中在公开 Responses interface，删除失去调用者的旧转换 unit tests | 提高 locality，避免实现重构导致多层重复测试一起变化 |
| 响应 Header 采用“默认保留端到端字段、明确过滤逐跳和当前连接字段”的策略，并用 ASGI raw_headers 保持重复项 | 同协议代理需要保留未知响应语义，同时不能转发 Connection 等逐跳状态 |
| `CLAUDE_BASE_URL` 显式必填且表示 `/v1` 之前的服务根 URL | 防止漏配时误请求 Anthropic，也避免 `/v1/v1/responses` |
| 首版请求 Header 仍采用受控 allow-list，不转发 Idempotency-Key/OpenAI-Beta | 延续已有鉴权隔离；这是明确兼容边界，后续可按名称扩充 |
| `Accept-Encoding` 例外进入受控允许集合，缺失或空值时发送 `identity` | 防止 HTTPX 自动代表不支持压缩的调用方声明 gzip，再把压缩 raw body交给该调用方 |
| 按 expand–contract 顺序实施六个串行 vertical issues | 先并存新旧路径、再删除旧路径，保证每个提交维持绿色 |
| `z-ai/glm-5.3-flash` 的最终真实矩阵覆盖四 role、两 assistant phase、三 content、string shorthand、function continuation 和 stream | 将用户的“全部 Responses 消息类型”转换为基于官方 message schema 的可重复完成条件 |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| 第一次目录探测命令因包含临时目录删除操作而被本地安全策略拦截，网络请求未发出 | 改用标准输入向 `curl` 传递敏感请求头并通过管道直接解析，随后目录请求成功 |
| 进度消息曾把“目录命中数量”和“最终成功数量”都写成 8 | 已核对并更正：目录命中 7 个，最终调用成功 8 个 |

## Resources

- OpenAI Responses create API：https://developers.openai.com/api/reference/cli/resources/responses/methods/create
- OpenAI easy input message schema：https://developers.openai.com/api/reference/cli/__sdk_schema?declaration=%28resource%29+responses+%3E+%28model%29+easy_input_message+%3E+%28schema%29&selected=%28resource%29+responses
- OpenAI Responses TypeScript create/stream API：https://developers.openai.com/api/reference/typescript/resources/beta/subresources/responses/methods/create
- HTTPX async streaming：https://www.python-httpx.org/async/
- Starlette StreamingResponse：https://www.starlette.io/responses/
- RFC 9110 HTTP Semantics：https://www.rfc-editor.org/rfc/rfc9110.html
- 当前入口：`src/api/endpoints.py`
- 当前上游客户端：`src/core/client.py`
- 当前套餐目录与路由：`src/core/zqi_catalog.py`
- 当前请求转换：`src/conversion/request_converter.py`
- 当前响应转换：`src/conversion/response_converter.py`
- 当前启动脚本：`start.sh`
- 当前 API 测试：`tests/test_api.py`
- 当前套餐测试：`tests/test_zqi_catalog.py`
- 已批准规划：`.scratch/openai-responses-proxy/plan.md`

## Visual/Browser Findings

- 用户图片列出 11 个待测模型：`WisGPT-5.6-Terra`、`WisGPT-5.6-Luna`、`WisGPT-5.6-Sol`、`deepseek/deepseek-v4-flash`、`deepseek/deepseek-v4-pro`、`openai/gpt-5.6-sol-cpr`、`openai/gpt-5.6-terra-cpr`、`openai/gpt-5.6-luna-cpr`、`z-ai/glm-5.3-flash`、`qwen/qwen3.8-flash`、`360-Wiscode-Multimodal`。
- 官方文档确认 Responses 创建接口使用 `POST /responses`，文本输入可以直接放在 `input` 字段中。

## 开放决策

- 当前没有待用户决定的设计项；用户已经整体批准 plan，并要求直接生成 spec/issues 后实施到完成。
- 用户新增的真实验收条件已按官方 message schema 固化为有限矩阵；如果真实上游出现能力差异，先基于原始请求/响应证据诊断，不能擅自换模型或删减矩阵。

## 外部方案比较

| 方案 | 收益 | 代价 | 当前倾向 |
|------|------|------|----------|
| 重新建模并序列化完整 Responses schema | 本地类型提示较强 | 容易落后于上游新字段，违背透明转发目标 | 不采用 |
| 读取原始请求体，仅解析路由所需最小字段，原字节转发 | 最大化未来兼容性，职责最少 | 本地不能替上游做完整 schema 校验 | 推荐 |
| 同时保留 Chat Completions 转换和 Responses 透明入口 | 兼容两类客户端 | 分支不再是用户要求的极简版本 | 默认不采用 |

## 被拒绝方案

- 不把 Responses 请求转换成 Claude Messages，再转换回 Responses；该往返没有业务收益，还会丢失未知字段与原生 SSE 事件。
- 不在智企目录请求失败、认证失败或目录数据损坏时静默使用普通密钥；“未命中模型”和“无法可靠判断是否命中”不是同一种状态。
- 不把调用方提供的 `Authorization` 或 `x-api-key` 原样发给上游；上游鉴权必须由代理根据套餐或 `.env` 控制。

## Spec/Issue 覆盖自审

- `REQ-01` 至 `REQ-41` 均已写入 `spec.md`；`SCN-01` 至 `SCN-32` 均包含明确 Given、When、Then。
- Issue 01 覆盖 `REQ-01/03-06/15-23/25/27-28/34-35` 与 `SCN-01/07-15/21-23`，交付普通密钥非流式完整纵切片。
- Issue 02 覆盖 `REQ-07-17/28/34-35` 与 `SCN-01-07/10-11`，交付智企 Responses 路由与严格回退边界。
- Issue 03 覆盖 `REQ-14/16-18/22-28/34-35` 与 `SCN-02/11-12/16-20/32`，交付原生 SSE、取消与清理。
- Issue 04 覆盖 `REQ-01-02/29/32-35` 与 `SCN-24/26/32`，执行旧 Chat/Claude contract 删除。
- Issue 05 覆盖 `REQ-19-20/29-35` 与 `SCN-22-26/32`，收口 `start.sh`、配置、元数据和文档。
- Issue 06 覆盖 `REQ-28/35-41` 与 `SCN-27-32`，执行 11 模型和指定 GLM 全消息类型真实闭环。
- 联合覆盖矩阵没有遗漏 requirement 或 scenario；重叠项用于跨 slice 回归，不存在只有内部实现测试而没有 public interface 验证的行为。
- Blocking graph 为 `01 → 02 → 03 → 04 → 05 → 06`，无环；每个 issue 都受真正的可运行前置条件阻塞。
- Issue 01 至 03 是 expand/migrate，Issue 04 是 contract，Issue 05 收口公开面，Issue 06 是真实完成门禁；每个 slice 适合一个 fresh worker context。
- 最高层 test seams 已固定为 TestClient、直接 ASGI driver、真实 resolver、隔离 shell 和运行中的本地服务五类。
- 独立规划审阅提出的非流式中断、gzip raw bytes、多值 Header、取消范围、Base URL、expand–contract 与 Accept-Encoding 歧义均已关闭；最终复审结论为“可审批”。

---

*每两次重要读取、搜索、实验或浏览后更新本文件。*

## Execution Context

- Review Base Commit: `9d727a841a6dde17fc81ac2363aae5a42fee6367`
