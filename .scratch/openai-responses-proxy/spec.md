# OpenAI Responses 极简透明代理

## 问题陈述

当前服务对外接收 OpenAI Chat Completions 请求，内部把它转换成 Claude Messages，请求上游后再把结果转换回 Chat Completions。

新的上游已经原生支持 OpenAI Responses。继续执行双向转换会增加字段丢失、事件改写和未来协议不兼容的风险，也会让智企套餐路由与协议转换相互纠缠。

用户需要一个独立的 `openai-responses` 分支。该分支只代理 OpenAI Responses 创建请求，不理解或重写业务内容，只处理代理访问鉴权、智企套餐选择、普通密钥回退、必要请求头和连接生命周期。

## 目标

- 对外只提供 `POST /v1/responses` 推理入口。
- 将调用方提供的 Responses JSON 原始字节发送给同协议上游。
- 将上游非流式 body 或流式 SSE 原始字节返回给调用方。
- 模型命中智企目录时使用可用套餐密钥和套餐请求头。
- 模型完全未命中智企目录时使用 `.env` 普通密钥。
- 保留代理访问鉴权、请求上下文头、脱敏日志和可靠资源清理。
- 保留根目录 `start.sh`，并把它作为首选启动入口。
- 通过自动测试、本地代理真实请求和完整消息类型模拟证明最终行为。

## 非目标

- 不继续支持 `/v1/chat/completions`。
- 不把 Responses 转换为 Claude Messages，也不把 Claude Messages 转换为 Responses。
- 不在本地复制完整 Responses schema。
- 不实现 Retrieve、Cancel、Delete、Input Items、Input Tokens 或 Compact 等其他 Responses resource 方法。
- 不提供模型别名、大小写归一化或模型能力补丁。
- 不在目录认证、请求或契约失败时绕过智企策略。
- 不保证下游 HTTP chunk 边界与上游相同。
- 不在首版实现非流式全生命周期的独立断连监听任务。
- 不穷举或强制模型生成全部 Responses 输出 item 和全部 SSE 事件；输出类型由上游模型决定，代理负责透传实际产生的所有类型。

## 解决方案

调用方把原生 Responses 请求发送到本地代理。代理先校验自己的访问密钥，再从原始 body 的旁路解析结果中只取得顶层 `model` 和 `stream`。

代理成功读取智企目录后，按模型精确匹配和 `responses` 协议能力选择套餐。命中可用套餐时使用套餐密钥；目录中完全没有模型时使用 `.env` 普通密钥。其他目录或套餐失败均明确返回错误，不使用普通密钥绕过。

代理使用配置的服务根 URL 拼接 `/v1/responses`，以原始 body bytes 请求上游。非流式和流式响应都从上游原始字节流读取。代理只过滤当前 HTTP 连接不能安全复用的响应头，不解释 Responses 对象或 SSE 事件。

## 用户故事

1. 作为 Responses 客户端，我想把原生请求直接交给代理，从而不因协议转换丢失字段。
2. 作为 Responses 客户端，我想使用字符串形式的 `input`，从而兼容最简单的创建请求。
3. 作为 Responses 客户端，我想发送 `message` item，从而表达多轮和分角色上下文。
4. 作为 Responses 客户端，我想使用 `user`、`assistant`、`system` 和 `developer` 角色，从而保留官方指令层级和历史消息语义。
5. 作为 Codex 类客户端，我想保留 assistant 消息的 `commentary` 和 `final_answer` phase，从而不丢失阶段信息。
6. 作为多模态客户端，我想发送 `input_text`、`input_image` 和 `input_file` 内容，从而使用官方消息内容变体。
7. 作为工具调用客户端，我想完成 function call 与 function call output 的真实两阶段交互，从而证明代理不会破坏 agent 上下文 item。
8. 作为流式客户端，我想收到上游原生 Responses SSE，从而及时显示增量结果。
9. 作为未来版本客户端，我想让代理保留未知 JSON 字段和未知 SSE 事件，从而减少上游升级导致的兼容问题。
10. 作为智企用户，我想让目录中的 Responses 模型优先使用套餐，从而使用企业额度和转发策略。
11. 作为智企用户，我想在模型完全未进入目录时继续使用普通密钥，从而仍能调用网关支持的其他模型。
12. 作为套餐管理员，我想让过期、耗尽或禁用套餐被跳过，从而不会错误使用不可用额度。
13. 作为套餐管理员，我想在模型存在但不支持 Responses 时看到明确错误，从而不会由普通密钥绕开目录限制。
14. 作为服务维护者，我想保留代理自身的 API Key 校验，从而控制谁可以访问本地代理。
15. 作为服务维护者，我想阻止调用方凭据覆盖上游凭据，从而避免身份混淆和凭据泄漏。
16. 作为链路维护者，我想保留或生成 task/trace/request ID，从而能跨服务定位一次请求。
17. 作为 HTTP 客户端，我想收到上游真实状态、body、压缩方式和端到端响应头，从而正确处理成功与失败。
18. 作为 HTTP 客户端，我想保留多个同名响应头，从而不丢失独立的 `Set-Cookie` 或认证挑战。
19. 作为 HTTP 客户端，我想让压缩协商尊重我的 `Accept-Encoding`，从而不会收到自己未声明支持的压缩 body。
20. 作为断开连接的客户端，我想让正在执行的流式上游请求停止，从而避免继续消耗连接和额度。
21. 作为部署者，我想通过 `start.sh` 从任意目录启动服务，从而不需要记住虚拟环境细节。
22. 作为部署者，我想在 `.venv` 缺失或损坏时自动修复，从而保持单命令启动。
23. 作为部署者，我想继续使用现有 `.env` 变量名，从而不需要迁移本地密钥。
24. 作为维护者，我想让旧 Chat/Claude 代码从该分支退出，从而让产品职责保持单一。
25. 作为验收者，我想通过本地代理真实调用 11 个候选模型，从而证明路由与普通密钥回退不是模拟结果。
26. 作为验收者，我想用 `z-ai/glm-5.3-flash` 完成全部官方消息角色和内容类型模拟，从而证明真实 agent 请求可以通过。

## 可观察 Requirements

- `REQ-01`：服务必须公开接受 `POST /v1/responses`，并且 `/v1/chat/completions` 必须返回 404。
- `REQ-02`：服务只能实现 Responses Create；其他 Responses resource 路径不属于本次公开能力。
- `REQ-03`：配置了 `PROXY_API_KEY` 时，只有匹配的 Bearer 或 `x-api-key` 才能进入目录和模型上游；未配置时保持当前免校验行为。
- `REQ-04`：代理必须读取并发送完全相同的请求 body bytes，不得通过对象序列化重建 body。
- `REQ-05`：代理只可从旁路 JSON 解析结果读取顶层 `model` 和 `stream`；其余字段必须交给上游验证。
- `REQ-06`：body 非 JSON object、`model` 非字符串或仅含空白、`stream` 非 boolean、顶层 `model`/`stream` 重复时必须返回 400，且不得请求目录或模型上游。
- `REQ-07`：用于目录匹配的 model 必须与请求原值一致，不裁剪、不改写，并区分大小写。
- `REQ-08`：目录模型的 `apiNames` 必须包含区分大小写的精确值 `responses` 才能作为套餐候选。
- `REQ-09`：可用套餐必须继续按内网、外网、未知 identifier 的优先级选择；同组按目录顺序选第一个可用项。
- `REQ-10`：过期、`exhausted=true` 或模型 `enabled=false` 的套餐不得使用；`expireAt` 只过滤，不参与排序。
- `REQ-11`：只有目录成功且模型完全未出现时，代理才可使用 `.env` 普通密钥；优先级为 `CLAUDE_API_KEY` 后 `ANTHROPIC_API_KEY`。
- `REQ-12`：模型已出现但不支持 Responses 时必须返回 400；支持 Responses 但全部套餐不可用时必须返回 503；两种情况均不得使用普通密钥。
- `REQ-13`：WisCode auth、目录网络、目录 JSON 或目录契约失败必须沿用明确的 401/500/502，且不得使用普通密钥。
- `REQ-14`：命中套餐时必须使用套餐 key，并按现有契约注入 `X-Ai-Forward-Url`、`X-Pkg-Model` 和可用的 `X-Ai-Forward-Email`。
- `REQ-15`：未命中套餐时不得包含三个套餐头；普通 key 缺失时必须在模型上游请求前返回脱敏的 500 配置错误。
- `REQ-16`：上游请求必须由代理控制 `Content-Type`、`Authorization`、`x-api-key`、`x-src`、request/task/trace ID；调用方凭据和其他未允许请求头不得覆盖或进入上游。
- `REQ-17`：调用方的非空 task/trace ID 必须原值保留；缺失或空白时必须分别生成不同值，并在同一请求生命周期复用。
- `REQ-18`：调用方提供非空 `Accept-Encoding` 时必须按列表语义转发；缺失或空值时必须显式向上游发送 `identity`。
- `REQ-19`：`CLAUDE_BASE_URL` 必须显式配置为 `/v1` 之前的绝对 HTTP(S) 服务根 URL；缺失、带 query/fragment 或已以 `/v1`、`/v1/responses` 结尾时必须返回脱敏的 500 配置错误。
- `REQ-20`：当前 `CLAUDE_BASE_URL=https://code.jizhi.360.cn/aiproxy` 必须唯一解析为 `https://code.jizhi.360.cn/aiproxy/v1/responses`。
- `REQ-21`：完整取得非流式上游响应后，代理必须保留上游 status、raw body bytes 和经过过滤的端到端响应头，不得解析或包装成功、错误或非 JSON body。
- `REQ-22`：响应头必须默认保留端到端字段及重复项，只过滤 `Connection` 及其动态声明字段、标准逐跳字段、`Content-Length`、`Server` 和 `Date`。
- `REQ-23`：非流式和流式读取都必须保持压缩 raw body 与 `Content-Encoding` 配对，不得自动解压后继续发送原压缩头。
- `REQ-24`：`stream=true` 时必须原样转发上游实际产生的 SSE bytes，包括空行、注释、多行 data、未知事件和终止事件；不得新增 `[DONE]` 或代理自定义 Responses 事件。
- `REQ-25`：下游响应开始前的上游连接或 body 读取中断必须返回 502，超时必须返回 504；下游响应开始后的错误只能终止 body 并清理资源。
- `REQ-26`：ASGI 请求任务取消必须向当前上游操作传播；流式断连或下游发送失败必须停止上游读取。
- `REQ-27`：上游 response、HTTP client 和活动请求记录必须最终清理，每项资源最多关闭一次。
- `REQ-28`：日志不得记录请求 body、响应 body、普通 key、套餐 key、Authorization、`x-api-key` 或 WisCode access token。
- `REQ-29`：根路径和 CLI help 必须只描述 OpenAI Responses Proxy；健康检查只表达进程和本地配置状态，不得声称真实上游可用。
- `REQ-30`：根目录 `start.sh` 必须保留可执行性、支持任意工作目录、修复缺失或不完整 `.venv`、避免重复同步完整环境，并原样传递 CLI 参数。
- `REQ-31`：`.env` 必须继续由 `python-dotenv` 加载；`start.sh` 不得 source `.env`；默认监听端口必须统一为 7072。
- `REQ-32`：外部变量名 `CLAUDE_BASE_URL`、`CLAUDE_API_KEY`、`ANTHROPIC_API_KEY`、`PROXY_API_KEY` 必须兼容；`ANTHROPIC_VERSION` 和 `anthropic-version` 必须退出运行契约。
- `REQ-33`：该分支最终不得保留可达的 Chat/Claude 转换路径；项目名和 console script 必须改为 `openai-responses-proxy`。
- `REQ-34`：实现不得新增 Python 依赖；新增固定值必须集中在常量模块；新增函数必须职责单一并包含说明职责、边界和失败清理原因的详细中文注释。
- `REQ-35`：最终自动测试必须覆盖请求字节、路由、鉴权、响应字节、压缩、重复 Header、流式、错误边界、取消、资源关闭和启动脚本。
- `REQ-36`：最终必须通过本地代理重新请求图片中的 11 个模型，记录 HTTP、Responses status、error 和文本；HTTP 200 不能单独算成功。
- `REQ-37`：最终必须用 `z-ai/glm-5.3-flash` 通过本地代理验证字符串 input，以及 `message` 的 `user`、`assistant`、`system`、`developer` 四种角色。
- `REQ-38`：最终必须用同一模型验证 assistant 的 `commentary`、`final_answer` phase，以及消息 content 的 `input_text`、自包含 `input_image`、自包含 `input_file` 三种官方稳定变体。
- `REQ-39`：最终必须用同一模型完成一次真实 function call → function call output 两阶段交互，并完成至少一次原生流式响应。
- `REQ-40`：`REQ-37` 至 `REQ-39` 的每一项都必须得到 2xx、最终 `status=completed`、没有非空 error 和可验证输出；非流式响应必须显式返回 `error=null`。流式终态若省略 nullable `error` 字段，验收必须记录该上游契约偏差，但透明代理不得改写 SSE，且该项仍可凭 `response.completed`、无非空 error 与正确输出判定为功能通过。任何其他失败都不得被记为通过，必须继续诊断和修正。
- `REQ-41`：真实验收工具和结果记录不得持久化任何密钥；测试输入资源必须自包含、微小且可重复运行。

## Scenarios

- `SCN-01`：Given 服务已配置普通密钥且目录中完全没有请求模型，When 客户端发送合法非流式 Responses 请求，Then 上游收到原 body、普通密钥和非套餐头，下游收到完整上游响应。
- `SCN-02`：Given 目录中存在支持 `responses` 的可用套餐，When 客户端发送请求，Then 套餐密钥覆盖普通密钥，并携带完整智企套餐头。
- `SCN-03`：Given 同一模型存在多个套餐，When 内网候选不可用而外网或未知候选可用，Then 代理按内网→外网→未知及目录顺序选第一个可用套餐。
- `SCN-04`：Given 模型存在但 `apiNames` 仅包含 `messages` 或大小写错误的 `Responses`，When 请求该模型，Then 返回 400 且不走普通密钥。
- `SCN-05`：Given 模型存在且支持 Responses 但候选全部过期、耗尽或禁用，When 请求该模型，Then 返回 503 且不走普通密钥。
- `SCN-06`：Given auth 文件、目录请求、JSON 或 schema 失败，When 任意模型请求到达，Then 返回对应 401/500/502 且不走普通密钥。
- `SCN-07`：Given 模型完全未命中且普通密钥缺失，When 请求到达，Then 返回脱敏 500 且不请求模型上游。
- `SCN-08`：Given 请求 body 不是 JSON object，或路由字段缺失、类型错误、仅空白或重复，When 请求到达，Then 返回 400 且目录和模型上游均未调用。
- `SCN-09`：Given 请求包含 Unicode、空白、未知字段、嵌套 input、tools 或 reasoning，When 代理请求上游，Then 捕获到的 body bytes 与调用方发送值完全相同。
- `SCN-10`：Given 调用方同时提供代理凭据、伪造上游凭据和无关 Header，When 请求通过代理鉴权，Then 只有代理选择的上游凭据与允许头进入上游。
- `SCN-11`：Given 调用方提供非空 task/trace ID，When 发送流式或非流式请求，Then 两个值原样保留；Given 值缺失或空白，Then 生成两个不同 ID 并在请求内复用。
- `SCN-12`：Given 调用方未提供或提供空 `Accept-Encoding`，When 代理请求上游，Then 上游收到 `identity`；Given 调用方声明 gzip，Then 上游收到等价列表值。
- `SCN-13`：Given 上游返回 200、402、429 或 500 及 JSON/文本 body，When 非流式 body 完整读取，Then status、raw body 与允许的端到端 Header 原样返回。
- `SCN-14`：Given 上游返回 gzip body、匹配的 Content-Encoding、重复 Set-Cookie 和 Connection 动态字段，When 响应通过代理，Then gzip bytes 和重复端到端字段保留，所有静态/动态逐跳字段被移除。
- `SCN-15`：Given 上游非流式 body 在完整读取前连接中断或超时，When 下游响应尚未开始，Then 分别返回脱敏 502 或 504，并只关闭资源一次。
- `SCN-16`：Given 上游返回包含空行、注释、多行 data 和未知事件的 SSE，When `stream=true`，Then下游拼接 bytes 相同且没有代理追加 `[DONE]`。
- `SCN-17`：Given 流式上游已经返回 4xx/5xx 响应，When 下游开始响应，Then status、允许 Header 和实际 body 原样发送。
- `SCN-18`：Given 下游流已经开始而上游随后失败，When 代理捕获异常，Then 只终止连接、记录脱敏日志并清理，不注入事件或改写状态。
- `SCN-19`：Given 上游连接或读取被 gate 阻塞，When ASGI 请求任务被取消，Then 无需释放 gate 即可观察取消向上传播且活动资源清零。
- `SCN-20`：Given 流式客户端发送 `http.disconnect` 或下游 send 失败，When 上游仍在等待下一块，Then 上游读取停止且资源只关闭一次。
- `SCN-21`：Given 配置了 `PROXY_API_KEY`，When 客户端凭据错误，Then 返回 401 且不读取目录、不请求模型；When 凭据正确，Then请求继续。
- `SCN-22`：Given `CLAUDE_BASE_URL` 缺失或非法，When Responses 请求到达，Then 返回脱敏 500 且不访问目录或模型上游。
- `SCN-23`：Given 当前合法 Base URL，When 构造目标 URL，Then 结果恰为 `https://code.jizhi.360.cn/aiproxy/v1/responses`。
- `SCN-24`：Given 服务构建完成，When访问根路径、健康检查和 CLI help，Then 只出现 Responses 产品语义，健康检查不把存活误报成真实调用成功。
- `SCN-25`：Given 从仓库内或外运行 `start.sh`，When `.venv` 缺失、不完整或完整，Then分别创建/修复或直接复用环境，并把所有参数原样交给应用。
- `SCN-26`：Given 最终分支，When 请求旧 Chat 路径或搜索可达调用链，Then旧路径返回 404 且没有可达转换器。
- `SCN-27`：Given 本地服务连接真实上游，When 图片中的 11 个模型逐项请求，Then每项都记录路由、HTTP、Responses status、error 和文本，且透明的上游额度错误不被代理改写。
- `SCN-28`：Given 模型 `z-ai/glm-5.3-flash`，When分别发送字符串 input 与四种 message role，Then每项均完成且返回可验证文本。
- `SCN-29`：Given 同一模型，When分别发送 assistant commentary/final_answer、input_text、自包含 input_image 和自包含 input_file，Then每种消息变体均得到 completed、error null 和内容相关输出。
- `SCN-30`：Given 模型可被强制调用一个本地定义的 function tool，When先取得 function_call 再回送匹配的 function_call_output，Then第二阶段得到 completed 最终文本。
- `SCN-31`：Given 同一模型和 `stream=true`，When真实请求完成，Then事件流包含 `status=completed`、无非空 error 和正确最终文本的原生 `response.completed`，没有代理额外添加的字段、事件或 `[DONE]`；若上游省略 nullable `error` 字段，则另行记录兼容性偏差。
- `SCN-32`：Given自动测试、真实验证和日志已经完成，When执行敏感信息扫描，Then源码、测试、日志与提交中均没有真实密钥或 access token。

## 关键决策

- “透明”指请求/响应协议内容不转换，不代表调用方可以控制上游鉴权。
- 普通密钥回退只发生在“目录成功且模型完全未出现”，不发生在无法判断目录或套餐被限制时。
- 该分支是独立产品面，旧 Chat Completions 入口直接退出，不承担兼容责任。
- 请求头采用受控允许集合；响应头默认保留端到端字段后过滤逐跳字段。
- 请求 body 原字节发送，但代理会在内存中旁路解析一次完整 JSON；首版不新增代理自定义大小上限。
- Base URL 继续使用用户现有环境变量名，但不再提供 Anthropic 默认值。
- 为保证每次提交可运行，实施采用 expand–contract：先并存新旧路径，再删除旧路径。
- 为把“所有 Responses 消息类型”变成可验证范围，本 spec 以官方 `easy_input_message` 当前稳定 contract 为准：四种 role、两种 assistant phase、`input_text`、`input_image`、`input_file`，并补充字符串 shorthand 和 function call continuation。内置工具、MCP、音频输出和模型选择的其他 output item 不属于“消息类型”。
- 真实消息类型验收必须使用用户指定的 `z-ai/glm-5.3-flash`，不能替换成另一个更容易通过的模型。

## 实施决策

- Endpoint module 只编排代理鉴权、原 body 读取、最小信封解析、上下文解析、route 解析、上游调用和下游响应选择。
- 最小信封 module 使用保留 object pairs 的标准 JSON 解码，只检查顶层 object 与路由字段，从而发现重复 key。
- Route module 在 expand 阶段暂时支持内部协议参数，以便新旧入口并存；contract 阶段收窄为只解析 Responses。
- Responses upstream module 以小 interface 隐藏 URL、受控请求头、套餐/普通 key、raw body、raw headers、超时和资源清理。
- 非流式与流式都以手动 streaming 模式打开上游并使用 raw iterator；非流式在下游开始前聚合全部 raw chunks。
- 响应 Header 使用 ASGI raw header 列表，保持顺序与重复项，并基于所有 Connection 字段动态扩充过滤集合。
- 请求上下文只解析一次，在同一请求的流式或非流式路径复用。
- 取消异常必须单独覆盖，不能只捕获普通 Exception。
- 固定字符串、路径、Header 名和稳定错误消息集中在常量 module。
- 最终提供可重复的真实 Responses 验证工具；它从进程环境读取本地代理地址和可选代理访问 key，不读取或输出上游密钥。

## 错误行为与恢复

- 代理产生的错误使用稳定、脱敏的 `{"detail":"..."}` JSON。
- 上游完整响应不被包装成代理错误，即使它是 4xx/5xx 或非 JSON。
- 下游尚未开始时，上游超时返回 504，其他连接/读取失败返回 502。
- 下游已经开始时不能改写状态；只终止 body、关闭资源并留下脱敏日志。
- 目录缓存刷新失败不得继续使用已经失效的旧快照。
- 流中断不得合成 `response.failed`、`response.completed` 或 `[DONE]`。
- 真实验收任一消息类型失败时，记录最小脱敏证据，定位请求是否被代理改写、路由是否错误或上游是否拒绝，然后针对根因修正并重跑整组；不得把预期错误或部分结果算作通过。

## 兼容性

- Python 版本继续为 3.9 及以上。
- 不新增第三方依赖；现有 FastAPI、HTTPX、Uvicorn 和 python-dotenv 足够。
- 保留 `CLAUDE_BASE_URL`、`CLAUDE_API_KEY`、`ANTHROPIC_API_KEY`、`PROXY_API_KEY` 外部变量名。
- 移除 `ANTHROPIC_VERSION` 和旧 console script 是该分支有意的不兼容变更。
- distribution 与 console script 统一改名为 `openai-responses-proxy`；`start.sh` 是文档首选入口。
- `.env` 修改后需要重启进程；健康检查不主动请求真实目录或模型。
- 真实目录、模型额度与网络是外部依赖；最终完成声明仍要求用户指定模型的全部消息类型实际成功。

## 测试决策

- 先在公开 HTTP interface 写失败测试，再实现行为，不通过辅助函数单测替代端到端观察。
- 旧路径在 expand 阶段继续通过；新 Responses 路径稳定后再删除旧测试和实现。
- 请求 body 比较 bytes，不比较重新解析后的对象相等。
- 响应 body 与 Header 保真通过直接 ASGI driver 观察，避免 TestClient 自动解压掩盖错误。
- 目录选择继续使用可控 auth、目录响应和时间输入覆盖既有边界。
- 流式测试包含未知事件、空行、注释、中途异常和幂等关闭。
- 真实验收同时解析 HTTP、Responses status、error、输出和 SSE 终态，不以 HTTP 200 单独判定。
- 每个 issue 完成后运行聚焦测试并提交；最终运行完整 suite、编译、shell 测试和真实模拟。

## Test Seams

- `TestClient + MockTransport`：通过公开 `POST /v1/responses` 验证代理鉴权、目录路由、目标 URL、原始请求 body 和受控请求 Header。
- 直接 ASGI `receive/send` driver + gated raw byte stream：捕获未经客户端解压的 raw body/raw headers，注入取消与 disconnect，验证 gzip、重复 Header、SSE 和清理。
- 真实 route resolver + 可控目录 transport：验证 exact model、Responses 协议、套餐优先级、状态过滤、缓存和 fail-closed 回退边界。
- 隔离 shell 环境：验证 `start.sh` 的 cwd、虚拟环境恢复、无重复同步和参数透传。
- 本地运行中的真实服务：通过 `127.0.0.1:7072/v1/responses` 验证 11 模型矩阵，以及 `z-ai/glm-5.3-flash` 的全部消息变体和原生流式终态。

## 范围之外

- Responses 的非 Create 方法。
- 全量转发任意调用方 Header；`Idempotency-Key`、`OpenAI-Beta` 等未列 Header 首版不转发。
- 服务端会话持久化、数据库、重试队列或熔断器。
- 对模型不支持的能力进行协议转换、内容降级或模型替换。
- 穷举 built-in tools、MCP、computer use、audio output、image generation 或官方 schema 中所有 output item。
- 修改 aiproxy、WisCode、CC Switch 或其他仓库。

## 补充说明

- OpenAI 官方当前把 message role 定义为 `user`、`assistant`、`system`、`developer`，并把稳定 message content 定义为 `input_text`、`input_image`、`input_file`。
- `assistant` 的 phase 可为 `commentary` 或 `final_answer`；真实验收会分别发送，不依赖模型主动生成这些 phase。
- 首轮直连证据显示 11 个图片模型中 8 个 completed，3 个 WisGPT 因额度返回 402；最终仍需通过实现后的本地代理重跑。
- 真实消息类型验收使用微小 data URL 图片和微小内嵌文件，避免依赖可变的第三方 URL。
