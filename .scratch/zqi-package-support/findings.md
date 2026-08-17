# Findings & Decisions

## Requirements
- 在 `/Users/qihoo/Documents/A_Own/claude-openai-proxy` 中继续使用现有 OpenAI Chat Completions -> Claude Messages 转换服务。
- 增加智企套餐模型支持，识别请求模型后附加 `X-Ai-Forward-Url`、`X-Pkg-Model` 等请求头。
- 调查 `/api/zqi/model-packages` 是否需要鉴权，以及最方便可靠的鉴权方式。
- 保留现有 `.env` 中 model-list 对应的 base URL/key 配置作为普通模型/兜底能力。
- 产出 Claude Code 与 Codex 场景的可落地方案；本轮先调查和决策，不直接改代码。

## Findings
- `claude-openai-proxy` 当前只有 `/v1/chat/completions` 入口；`src/api/endpoints.py` 将输入转换为 Claude Messages，再由 `src/core/client.py` 固定 POST `{CLAUDE_BASE_URL}/v1/messages`。
- 当前上游请求头只包含 `x-api-key`、`Authorization: Bearer <CLAUDE_API_KEY>`、`anthropic-version`、`x-src=ide`、请求/任务 trace 头；当前没有智企转发头。
- 当前 `ClaudeClient` 的 `api_key`、`base_url` 在模块导入时由 `.env` 全局配置初始化，不能按请求直接切换。
- 当前模型目录没有本地实现；模型名会原样透传，普通模型所有请求都使用同一组 `.env` 的 key/base URL。
- 当前 `aiproxy` 在 `/Users/qihoo/Documents/A_Finer/aiproxy` 中注册 `NewForwardIfRequested` 到路由最前面。请求带 `X-Ai-Forward-Url` 时进入直接转发，不进入 token/channel/billing/普通 relay。
- `X-Ai-Forward-Url` 的固定值在 aiproxy 中是 `https://llm.api.zyuncs.com/v1`；它被用于拼接最终请求 URL。`X-Ai-Forward-Email` 用于转发日志并在转发前删除。`X-Pkg-Model` 在 aiproxy 仓库没有读取逻辑，未被删除，会原样透传给最终上游；最终套餐消费逻辑不在 aiproxy 仓库。
- aiproxy 路径识别：`/v1/messages` -> Claude Messages；`/v1/chat/completions` -> OpenAI Chat；`/v1/responses` -> OpenAI Responses。带 forward URL 的 Claude Messages 请求通常透明转发到 `https://llm.api.zyuncs.com/v1/messages`。
- aiproxy 对 Responses 有特殊名单，会将部分 Responses 请求转成 Chat Completions；Kimi `moonshotai/kimi-k3` 不在该名单，且其 model-packages 数据只有 `messages, chat`，不应把 Kimi 当成 Codex Responses 原生可用模型。
- WisCode/kwoo-client 的本地 `/api/zqi/model-packages` 是 protected 路由：`server/index.js` 使用 `authenticateToken`；`server/routes/zqi.js` 明确从 SQLite auth_tokens -> 请求头 x-auth-access-token -> `~/.wiscode/auth.json` 的 access_token 回退，并向 `WISCODE_BASE_URL/api/zqi/model-packages` 发送 `Authorization: Bearer <access_token>`。
- kwoo-client 的 server-side catalog 也明确要求 access token，没有 token 会报认证错误；不是 cookie 方案。
- 用当前机器真实 access token 做了脱敏验证：直连 `https://wiscode.qihoo.net/api/zqi/model-packages?...` 不带鉴权返回 HTTP 401，带 Bearer access_token 返回 HTTP 200。只输出了状态码和响应前缀，没有持久化凭据。
- 直接请求 `https://code.jizhi.360.cn/api/zqi/model-packages` 得到 HTTP 404 Invalid URL；model-packages 的认证目录服务是 `WISCODE_BASE_URL`（当前本机为 `https://wiscode.qihoo.net`），不是模型网关 `code.jizhi.360.cn/aiproxy`。
- model-packages 响应中的 package.apiKey.full 是“模型实际请求 key”；查询接口的 Bearer access_token 是“读取套餐目录/套餐 key 的用户身份凭据”，两者职责不同。
- 已核对实际 model-packages 数据结构：套餐项含 `id`、`expireAt`、`exhausted`、`apiKey.full`、`models[]`；模型项含 `name`、`enabled`、`apiNames`。实际数据中存在同时支持 `chat`/`messages`/`responses` 的模型，也存在只支持 `chat`/`messages` 或只支持其他能力的模型。

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 推荐 proxy 自己读取 WisCode access_token，服务端请求 model-packages；不要把 access_token 暴露给 Chat/Codex 客户端 | 目录接口明确受保护，现有 kwoo-client 已有相同的 auth.json/DB 取 token模式；减少凭据泄露和客户端改造 |
| 推荐在 proxy 启动/定时刷新套餐目录并内存缓存，按 model 建立 `package_id + package_key + forward_url + protocol capabilities` 映射 | 避免每次对话请求 model-packages，降低延迟和 token 过期故障；model-packages 的 key 是短敏感数据，不能写普通日志 |
| 普通 `.env` base URL/key 保留作为默认普通模型路由；套餐模型命中后切换到 package key + forward headers | 满足兼容现有普通模型配置，同时支持套餐模型 |
| `X-Ai-Forward-Url` 对套餐请求应固定为 `https://llm.api.zyuncs.com/v1`；`X-Pkg-Model` 使用 model-packages 的 package id，而不是 model id | 与 aiproxy 当前消费/透传逻辑和 kwoo-client 现有实现一致 |
| 当前 proxy 的外部协议仍保持 Chat Completions；内部到 `aiproxy` 使用 Claude Messages。Codex 需要单独的 Responses->Chat 入口或复用已有 CC Switch 转换层，不应伪称当前 proxy 原生支持 Codex Responses | 当前 FastAPI Pydantic 模型和路由只支持 Chat Completions；直接发送 Responses 会 404/校验失败 |
| 本轮只修改 `claude-openai-proxy` 的 Claude Code 协议转换链路；不适配 Codex Responses，也不修改 Codex 所属仓库 | `claude-openai-proxy` 本身没有 Codex 实现，本次职责就是 OpenAI Chat Completions -> Claude Messages 的协议转换 |
| 按请求 `model` 精确匹配套餐目录；精确命中才使用套餐 key 和智企转发头，未命中继续使用现有 `.env` 的 `CLAUDE_BASE_URL`/`CLAUDE_API_KEY` | 保持现有普通模型兼容，避免把未知模型或目录暂时不可用误判成套餐模型 |
| 套餐目录不可用时不回退普通 `.env` 路由；若 `~/.wiscode/auth.json` 读取/JSON 解析失败、`access_token` 缺失或目录认证/请求失败，则返回明确错误，至少区分 `auth.json` 解析失败与目录上游失败 | 用户明确要求错误原因可见，避免使用普通 key 掩盖智企认证配置故障 |
| model-packages 地址使用 `~/.wiscode/auth.json` 的 `host` 字段，固定使用 `https://{host}/api/zqi/model-packages`；`host` 缺失或非法时返回明确配置错误，不要求用户额外设置目录 URL | 与直接消费现有 auth.json 的目标一致，避免 auth 环境与目录环境错配 |
| 套餐目录采用 30 分钟内存缓存；TTL 只控制目录刷新频率，不能替代每次请求前的套餐有效期检查。`auth.json` 的 `host` 或 `access_token` 变化时立即刷新；套餐上游认证失败时清除缓存并返回明确错误 | 目录不是按分钟变化的数据，30 分钟可降低目录请求；有效期检查和凭据变化检测避免因缓存使用过期套餐或旧登录态 |
| 套餐模型路由必须同时校验套餐有效期、`exhausted`、模型 `enabled` 和协议能力；当前代理发往上游的是 Claude Messages，因此只允许 `apiNames` 包含 `messages` 的模型进入套餐路由 | 不能仅按名称命中，否则会把视觉、语音、embedding 或不支持 Messages 的模型送入当前文本协议链路 |
| 同一模型命中多个有效套餐时，过滤后按 `expireAt DESC` 选择有效期更晚者；`expireAt` 相同时保持 model-packages 返回顺序取第一个；不读取、比较或校验 `package.id`，命中后只将其字符串化写入 `X-Pkg-Model` | `package.id` 的业务类型和排序语义由上游定义，proxy 不擅自解释；同时保留有效期优先规则 |
| 遍历 `data.list` 全部套餐，不按 `identifier`、`network` 或套餐名称额外过滤；这些字段仅可用于诊断/错误信息，不参与模型路由资格判断 | 避免把未来新增或不同标识的合法套餐错误排除，路由资格只由模型、协议能力、启用状态、有效期、额度和 key 决定 |
| 模型路由按四类结果区分：模型完全未出现在任何 `models[].name` 才走普通 `.env`；模型出现但相关字段契约错误返回 502；`apiNames` 合法但不含 `messages` 返回 400；支持 `messages` 但无可用套餐返回 503；后三类均不回退 | 只对真正未声明的模型保持普通兼容，避免把已声明但不可用/数据损坏的智企模型静默降级 |
| 智企错误响应只返回详细 `detail`，不新增稳定错误码字段；沿用 FastAPI 现有错误结构。`detail` 必须明确说明模型、套餐 ID（如可用）、字段路径、期望/实际类型或具体故障原因 | 保持接口改动最小，当前调用方直接消费可读错误，不引入新的错误码契约 |
| proxy 启动时不主动加载套餐目录；首次收到 Chat Completions 请求时按需读取 `auth.json` 并加载目录。由于只有目录确认模型完全不存在时才能走普通 `.env`，即使普通模型请求也必须经过目录解析；目录失败按既定规则报错 | 启动不依赖 WisCode 网络/登录态，同时保持“仅明确未声明模型才普通回退”的路由契约 |
| 所有 Chat Completions 请求都必须先完成套餐目录解析；普通 `.env` 路由仅在目录成功确认完整模型名未出现时使用。目录失败时普通模型也报目录错误，不维护普通模型白名单或命名绕过规则 | 保持精确模型边界，避免将实际智企模型误判为普通模型 |
| 套餐有效期使用严格比较 `expireAt > now`；`expireAt == now` 立即视为过期，比较前将 ISO 8601 时间统一转换为绝对时间戳 | 固定过期边界，避免依赖字符串格式或毫秒级边界歧义 |
| 命中套餐时完全替换普通认证：`x-api-key` 使用 `package.apiKey.full`，`Authorization` 使用 `Bearer package.apiKey.full`，不携带普通 `CLAUDE_API_KEY`，并增加 `X-Ai-Forward-Url`、`X-Ai-Forward-Email`、`X-Pkg-Model`；未命中时保持现有普通 headers 且不增加智企 headers | 避免两套 key 并存产生认证优先级歧义，严格区分普通路由和套餐路由 |
| 命中套餐不改变 proxy 实际连接的 `config.claude_base_url`；仍请求 `{CLAUDE_BASE_URL}/v1/messages`，由 aiproxy 消费 `X-Ai-Forward-Url` 后转发至 `https://llm.api.zyuncs.com/v1/messages`。不让 proxy 直连转发 URL，避免绕过网关或重复 `/v1` | 区分 proxy 的网关连接地址与 aiproxy 的最终转发指令，保持已核实链路 |
| 模型请求阶段收到 401/403 时不自动刷新套餐目录、不自动重试原请求；按现有脱敏上游错误直接返回。目录只按首次加载、30 分钟 TTL 或认证指纹变化刷新 | 避免重复执行带工具/副作用请求，保持目录刷新边界并区分目录加载故障与模型上游拒绝 |
| `auth.json.host` 只允许 hostname 和可选端口；拒绝协议、路径、查询和片段。proxy 固定使用 `https://{host}/api/zqi/model-packages`，host 非法时返回明确配置错误 | 防止 URL 拼接歧义和非预期路径/协议，保持目录接口地址固定 |
| 目录、套餐或模型字段不符合已确认契约时，必须直接返回对应 HTTP 错误并在 `detail` 中写清具体原因；不能只记录日志、静默忽略当前相关项或降级普通路由。日志仅作为辅助诊断，且不得包含 token/key | 用户明确要求契约问题可见、可定位，避免错误被日志吞掉或被普通路由掩盖 |
| 字段错误 detail 对非敏感字段可展示实际值、类型和路径；对 `access_token`、`apiKey.full`、Authorization、x-api-key 等敏感字段只展示缺失/空值/类型，不展示原文 | 在错误可定位与凭据不泄露之间保持边界 |
| `apiKey.full` 缺失、为空或类型错误属于目录字段契约错误，返回 502；`apiKey.full` 合法但套餐过期、`exhausted === true` 或模型显式禁用属于业务不可用，返回 503；两类 detail 分别写清字段原因或套餐状态 | 区分上游结构变更与合法套餐状态，便于定位且不泄露 key |
| 同一模型跨套餐按套餐分别判断 `apiNames` 和可用状态；只将支持 `messages`、未显式禁用、未过期、未耗尽且 key 合法的套餐纳入候选。仅当所有该模型项都不含 `messages` 返回 400；字段契约错误返回 502；支持 messages 但无可用套餐返回 503 | 避免某个套餐能力不足误伤同模型的其他有效套餐，并保持错误分类准确 |
| 不比较或去重不同套餐的 `apiKey.full`；即使 key 相同，也按套餐独立候选，仍按 `expireAt DESC` 和原始返回顺序选择，`X-Pkg-Model` 使用选中套餐原始 `id` 字符串 | 相同 key 不代表套餐消费语义可互换，proxy 不推断上游 key 复用关系 |
| `X-Ai-Forward-Email` 复用 aiproxy 的校验边界：`mail` 非空、长度不超过 320、不含 CR/LF 且包含 `@` 才发送；否则省略该可选头，不影响套餐请求 | 保持 proxy 与 aiproxy 的日志归属规则一致，不新增不一致的邮箱校验语义 |
| 每次模型请求都基于当前目录缓存快照重新执行 exact model、`apiNames`、`enabled`、`expireAt`、`exhausted` 和 `apiKey.full` 过滤及套餐选择；30 分钟 TTL 只控制远程目录刷新，不延长套餐有效期 | 即使缓存未到期，也能在本地及时识别过期/耗尽/禁用状态并返回明确错误 |
| `auth.json.mail` 是可选归属字段，不参与目录鉴权或模型认证；缺失、为空或非法时只省略 `X-Ai-Forward-Email`，套餐路由继续。`host`/`access_token` 仍是必需字段，问题时按既定错误返回 | 区分必要认证字段与可选日志字段，保持与 aiproxy 的消费逻辑一致 |
| `auth.json` 顶层必须是 object；JSON 解析失败或顶层类型错误返回 500。`host` 缺失、null、非字符串或格式非法返回 500；`access_token` 缺失、null、非字符串或空字符串返回 401。敏感 token detail 只展示路径和状态，不展示原文 | 明确区分本地配置文件损坏与外部认证凭据缺失/无效 |
| 每次请求只读取一次 `auth.json`；读取失败、JSON 解析失败或字段处于缺失/非法状态时立即按具体原因返回，不因文件写入竞态重读，不回退旧 token、localStorage 或普通 `.env` | 保持本地认证文件错误语义严格且可观测，避免隐藏登录态文件问题 |
| 套餐目录只严格校验路由实际使用的字段：套餐 `id`（仅需可转为单值 Header 字符串）、`expireAt`、`exhausted`、`apiKey.full`、`models`，以及模型 `name`、`apiNames`、`enabled`；`name`、`identifier`、`network`、`quotas` 等不参与路由的字段不作为硬依赖 | 保持字段契约错误可见，同时不让与协议路由无关的展示字段变化阻断请求 |
| 当前请求模型关联的任意套餐/模型项存在路由字段契约错误时，即使另有合法可用套餐，也返回 502 并指出具体路径；不忽略坏项、不使用其他套餐、不回退普通路由。只有所有关联项结构合法后，才按 `messages` 能力和套餐状态继续判断 | 遵循“相关字段契约错误必须报错”，避免目录返回顺序变化导致不稳定路由或掩盖上游数据问题 |
| 任何套餐/模型项的路由字段契约错误都使整个目录快照无效并返回 502，不区分是否与当前请求模型相关；非路由字段异常不影响路由。错误 detail 指出完整字段路径、期望类型和实际类型，不缓存、不回退普通 `.env` | 先完整验证路由契约，避免坏项被误判为未命中或隐藏当前模型；同时不把展示字段变化变成硬依赖 |
| 目录刷新失败、响应非法或路由字段契约校验失败时，清除当前认证指纹对应的旧快照；后续请求不得使用旧快照，只能重新刷新并再次成功校验后建立新快照 | 旧目录的有效性无法证明，必须与已确定的“刷新失败废弃旧缓存”一致 |
| `data.list=[]` 和合法套餐 `models=[]` 都是合法空集合：前者缓存空目录并允许普通路由，后者不产生模型候选但不阻断其他套餐。仅缺失或非数组的 `data.list`/`models` 返回 502 | 区分合法“没有数据”与字段契约错误，保持严格校验但不误报空集合 |
| 每个套餐对象即使 `models=[]` 也必须具备合法的 `apiKey.full`；缺失、空值或类型错误返回 502，目录快照整体无效 | 套餐路由字段契约对空模型套餐同样成立，避免缓存未经验证的套餐对象 |
| 套餐 `id` 不做数值或业务类型校验，但字段必须存在、非 `null` 且可转换为单值 Header 字符串；缺失/null/对象/数组等无法作为单值 Header 时返回 502 并指出路径，其他值直接字符串化写入 `X-Pkg-Model` | 保持对 ID 业务语义无假设，同时满足 HTTP Header 必须是单值字符串和网关需要套餐标识的约束 |
| `data.list` 中任意套餐项不是 object 都使目录快照无效并返回 502，detail 指出数组下标、期望类型和实际类型；不缓存、不回退普通路由 | 套餐集合中的每个元素都必须满足可校验的对象契约，避免坏项被当作模型未命中 |
| 后续未单独询问的实现细节默认采用当前 findings 中标注的推荐方案；只有发现与已确认决策冲突的实质性问题时才再次提问 | 用户要求后续问题按推荐方案处理，减少重复确认，不改变已确认的行为边界 |
| 目录成功响应必须同时满足 HTTP status 为 2xx、`context` 为 object、`context.code === 0`、`data` 为 object、`data.list` 为 array；`context.code` 非 0 或字段缺失/类型错误按具体原因返回 502，`context.message` 只作为错误详情辅助，不单独决定成功 | 固定上游成功判定，区分业务错误和字段契约错误，避免把任意 message 当作成功 |
| 若请求模型存在于套餐目录，但其模型项 `apiNames` 不包含 `messages`，直接返回明确的协议不支持错误，不回退普通 `.env`；只有模型完全不存在于套餐目录时才走普通路由 | `apiNames` 是上游声明的协议能力，避免用户选择的套餐模型被静默改走普通 key 或得到模糊的模型错误 |
| 模型存在于套餐目录但所有匹配套餐均不可用时，直接返回包含具体原因的错误，不回退普通 `.env`；原因至少区分过期、额度耗尽、模型禁用、缺少 `apiKey.full` 和无有效套餐 | 用户要求错误原因清晰，避免把智企套餐故障伪装成普通上游模型错误 |
| 套餐模型只使用完整 `model` 名称与模型项 `name` 的 exact match，不支持去掉 namespace 的 basename 或其他隐式别名 | 保持模型路由确定性，避免不同 namespace 下同名模型误匹配 |
| 命中套餐后保持请求体中的原始完整模型名；只用 `X-Pkg-Model` 指定套餐 `package.id`，并使用对应 `apiKey.full` 作为上游认证 key；请求头和字段取值遵循已核实的 aiproxy/智企目录链路，不自行改写为 `modelId` | `models[].name` 是协议请求模型名，`package.id` 是套餐选择标识，`modelId` 是目录内部模型标识，职责不同 |
| 命中套餐后附加 `X-Ai-Forward-Email`，取 `~/.wiscode/auth.json.mail`；仅在 mail 非空且格式合法时发送，缺失/非法时省略该可选头，不影响套餐请求 | aiproxy 使用该头做日志归属并在最终转发前删除；它不是模型认证凭据 |
| 每个 Chat Completions 请求独立解析一次 route；非流式与流式共用该 route，并将 `api_key`、`forward_url`、`package_id`、`forward_email` 等作为请求级不可变数据传入 Claude client，禁止通过修改全局 client 配置切换请求 | 避免并发普通请求与套餐请求之间串用 key、URL 或套餐请求头 |
| 智企路由错误使用明确 HTTP 状态：协议不支持 400、外部认证失效 401、本地 auth 文件/host 配置错误 500、目录上游网络或非法响应 502、套餐当前不可用 503；流建立前返回普通 FastAPI JSON，流建立后继续使用现有 SSE 错误格式 | 让 Claude Code 能区分请求错误、登录态错误、本地文件错误、目录上游故障和套餐不可用，而不是统一显示 500 |
| 查询套餐目录固定使用 `include_limit=true&include_keys=true&include_models=true`；内存只保留路由所需字段，日志不记录 `access_token` 或 `apiKey.full` | 路由必须同时获得 key、模型能力和额度/耗尽状态，避免依赖上游默认字段或泄露敏感凭据 |
| 套餐候选必须满足已确认的字段契约；任意套餐项的结构损坏、关键字段缺失或类型错误都使目录刷新失败并返回 502，不能因与当前模型无关而静默忽略。已合法识别的模型若套餐过期、耗尽或禁用，返回对应 503；模型不含 `messages` 返回 400 | 用户要求契约不符合时直接报错并写清原因，不能让坏数据被吞掉 |
| 独立 proxy 严格读取 `$HOME/.wiscode/auth.json`，不读取 `~/.wiscode-dev/auth.json`，不使用 `WISCODE_USER_DATA_DIR`、localStorage 或本地 JWT 作为回退 | 本次 proxy 不属于 Electron/kwoo-client 运行时，且目录接口需要的是 `auth.json.access_token` 外部 SSO 凭据 |
| 每个模型请求开始时读取 `$HOME/.wiscode/auth.json`，解析 `host`、`access_token`、`mail`；以 `host + access_token` 的内存指纹检测认证环境变化，指纹变化立即刷新套餐目录；token 不写日志或磁盘 | 不依赖 mtime/文件事件，能发现其他进程原地更新登录态，同时避免每次请求都访问远程目录 |
| 按 `host + access_token` 指纹对套餐目录刷新使用 single-flight；并发请求共享同一进行中的刷新任务，刷新结果仅能写入发起时仍匹配的认证代次/指纹，旧认证请求不得覆盖新缓存 | 避免缓存过期时的请求风暴，并隔离登录态切换期间的旧请求结果 |
| 目录缓存过期后刷新失败时废弃旧缓存，当前请求直接返回对应明确错误；不采用 stale-while-revalidate | 避免继续使用可能过期、耗尽、撤销或属于旧登录态的套餐 key |
| 目录刷新成功后先完整解析/校验，再以不可变完整快照原子替换旧缓存；请求已解析的 route 不受后续刷新影响 | 避免读取到套餐、模型能力和 key 的半更新状态，并保持请求级路由隔离 |
| 合法成功响应中的 `data.list=[]` 作为空目录快照缓存 30 分钟；请求模型因完全不在目录中而走普通 `.env`。仅当目录明确包含该模型但其套餐不可用时返回 503 | 区分“用户无智企套餐”和“已选择的智企模型当前故障”，避免空目录阻断普通模型 |
| 目录 HTTP 成功但响应结构变化/字段缺失时返回 502，并在错误中指出具体结构原因，例如 `响应缺少 data.list 字段`、`data.list 字段类型从数组变为对象`、`context.code 非成功值`、`响应不是合法 JSON`；不使用笼统的“格式异常”，不缓存且不回退普通路由 | 用户要求能直接判断上游字段契约发生了什么变化，便于修复而不是只看到模糊网关错误 |
| 任意套餐/模型项字段缺失、类型变化或结构损坏都返回 502，并指出具体数组下标、字段路径、期望类型和实际类型；不把坏项静默当作未命中而回退普通路由 | 用户要求所有契约问题直接暴露，避免数据变化被掩盖 |
| 同一套餐内出现重复的完整模型名时，视为目录契约错误并返回明确 502，指出套餐 ID、模型名和重复项路径；不合并、不依赖数组顺序 | 重复模型的能力、启用状态或字段可能冲突，强行合并会隐藏上游脏数据并改变路由语义 |
| `exhausted` 为 `null` 时按未耗尽处理，与 `false` 一样允许套餐进入候选；仅 `exhausted === true` 判定额度耗尽。这样兼容上游未提供确定额度状态的合法响应 | 用户选择可用性优先；`null` 不应被误判为套餐不可用，只有上游明确报告耗尽才阻断 |
| 模型 `enabled` 只有显式 `false` 才判定禁用；`true`、`null` 或字段缺失均按启用处理 | 兼容上游省略启用字段的合法目录，同时保留明确禁用信号 |
| 当前请求模型的 `apiNames` 必须是数组：数组包含 `messages` 才支持当前协议；数组不含 `messages` 返回 400 协议不支持；字段缺失、`null` 或非数组返回 502，并指出模型和字段契约原因 | 区分业务能力不支持与上游目录字段缺失/类型变化，避免错误分类 |
| `expireAt` 按 ISO 8601 解析为绝对时间戳；任意套餐缺失/非法都返回 502，并指出套餐索引、`package.id`（可表达时）和字段实际值/原因；不把非法值默认为已过期或永不过期 | 契约错误必须可见，不能被当作无关坏数据忽略 |
| `apiKey.full` 必须是非空字符串；任意套餐缺失、空值或类型错误都返回 502，并指出套餐索引、`package.id`（可表达时）和字段原因；错误与日志绝不包含 key 原文 | 区分目录字段契约错误与已合法识别套餐的业务不可用状态，并防止凭据泄露 |
| 不对套餐 `id` 做业务类型/数值校验；命中套餐后按上游返回值转换为 HTTP Header 可发送的字符串，`X-Pkg-Model` 原样表达该值。只有值缺失、`null` 或无法转换为单值 Header 时才无法发送并报目录字段错误；不改用 `identifier`、`models[].id` 或 `modelId` | `id` 的业务类型由上游定义，proxy 只负责按已核实协议透传套餐 ID；但 HTTP Header 本身只能承载字符串，不能把对象/数组直接写入请求头 |
| `X-Pkg-Model` 只取命中套餐对象的原始 `id` 字段并字符串化；不排序、不计算、不从 `identifier` 推导，也不使用模型项的 `id`/`modelId` | 套餐 ID 的语义和格式由上游负责，proxy 只做协议要求的字符串表达 |
| model-packages 刷新仅对网络连接失败和超时最多重试 1 次，退避约 200ms；401、非 JSON、字段契约错误和其他 HTTP 错误不重试，直接返回对应明确错误 | 吸收瞬时网络抖动，同时避免对认证/契约错误无效重试并放大上游故障 |
| model-packages 每次尝试使用 10 秒总超时；网络失败/超时后等待约 200ms 最多再试一次。超时错误说明尝试次数和目标 URL（不含 token） | 在目录前置依赖与用户失败反馈速度之间取平衡，同时避免泄露凭据 |
| TTL、目录请求超时、重试退避和最大重试次数固定为代码常量，不新增必需环境变量；测试通过注入时钟、HTTP transport 等依赖控制时间和网络 | 这些是已确认的行为契约，固定默认值可避免部署差异和配置错误 |

## Open Decisions
- 套餐目录鉴权 token 的来源：推荐让 proxy 读取 `~/.wiscode/auth.json`；备选是 `.env` 固定 `WISCODE_ACCESS_TOKEN`，简单但 token 过期需要手工更新。
- 是否允许 proxy 直接访问 `https://wiscode.qihoo.net` 并承担用户登录 token 生命周期；若部署环境没有该 auth.json，需要显式注入 token。
- `claude-openai-proxy` 不增加 `/v1/responses`，不在本仓库适配 Codex Responses；Codex 不属于本次改造范围。
- 套餐模型映射使用 exact model name；显式 `.env` JSON 覆盖暂不纳入本轮范围。

## Resources
- `/Users/qihoo/Documents/A_Own/claude-openai-proxy/src/api/endpoints.py`
- `/Users/qihoo/Documents/A_Own/claude-openai-proxy/src/core/client.py`
- `/Users/qihoo/Documents/A_Own/claude-openai-proxy/src/core/config.py`
- `/Users/qihoo/.codex-api/worktrees/8a13/kwoo-client/server/routes/zqi.js`
- `/Users/qihoo/.codex-api/worktrees/8a13/kwoo-client/server/services/official-model-catalog.ts`
- `/Users/qihoo/Documents/A_Finer/aiproxy/core/controller/relay-forward.go`
- `/Users/qihoo/Documents/A_Finer/aiproxy/core/relay/adaptor/openai/adaptor.go`
