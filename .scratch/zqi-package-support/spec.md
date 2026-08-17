# 智企套餐路由支持

## 问题陈述

当前代理只使用 `.env` 中的 `CLAUDE_BASE_URL` 和 `CLAUDE_API_KEY`，无法根据 Claude Code 请求的完整模型名选择 WisCode 智企套餐。需要在保持现有 OpenAI Chat Completions -> Claude Messages 转换的前提下，读取 WisCode 登录态、查询套餐目录，并为命中的模型使用套餐 key 和转发请求头。

## 目标

实现按请求模型选择普通路由或智企套餐路由；套餐目录通过 `$HOME/.wiscode/auth.json` 认证并缓存；路由和错误行为可预测、可诊断，且不泄露任何 token/key。

## 非目标

- 不增加 `/v1/responses`，不适配 Codex Responses。
- 不修改 `aiproxy` 或 `kwoo-client`。
- 不直连 `https://llm.api.zyuncs.com`；仍连接 `.env` 的 `CLAUDE_BASE_URL`。
- 不读取 localStorage、本地 JWT、`~/.wiscode-dev/auth.json` 或 `WISCODE_USER_DATA_DIR`。
- 不增加用户必须配置的智企环境变量。

## 解决方案

每个 Chat Completions 请求开始时读取 `$HOME/.wiscode/auth.json`，使用 `host` 和 `access_token` 请求：

```text
https://{host}/api/zqi/model-packages?include_limit=true&include_keys=true&include_models=true
```

目录按 `host + access_token` 指纹做 30 分钟内存缓存和 single-flight 刷新。每次模型请求基于当前快照重新过滤和选择套餐。模型完整名与 `models[].name` exact match；完全未出现的模型走普通 `.env`，已出现但不满足协议/套餐条件的模型返回明确错误。命中套餐时保留请求体 model 原值，并按请求级 route 切换 key 和转发头。

## 用户故事

1. 作为 Claude Code 用户，我想使用目录中支持 Messages 的智企模型，从而使用我的智企套餐额度。
2. 作为普通模型用户，我想在模型未出现在智企目录时继续使用现有 `.env` 配置，从而保持兼容。
3. 作为排障人员，我想看到具体的 auth、目录字段、协议能力或套餐状态错误，从而快速定位问题。
4. 作为系统维护者，我想让并发请求共享目录刷新且互不串 key，从而避免请求风暴和凭据污染。

## 可观察 Requirements

- REQ-01：公开入口仍只支持 `POST /v1/chat/completions`，请求仍转换为 Claude Messages。
- REQ-02：代理只读取 `$HOME/.wiscode/auth.json`；顶层必须是 object，`host` 和非空字符串 `access_token` 为必需字段，`mail` 可选。
- REQ-03：目录 URL 固定为 `https://{host}/api/zqi/model-packages?include_limit=true&include_keys=true&include_models=true`；host 只允许 hostname 和可选端口。
- REQ-04：目录成功必须是 HTTP 2xx、`context.code === 0`、`data` 为 object、`data.list` 为 array；空数组是合法空目录。
- REQ-05：路由字段必须完整合法：套餐 `id` 可转为单值 Header 字符串、`expireAt` 可解析为 ISO 8601、`exhausted` 为 boolean 或 null、`apiKey.full` 为非空字符串、`models` 为 array；模型 `name` 为字符串、`apiNames` 为 array、`enabled` 为 boolean 或 null/缺失。
- REQ-06：任何路由字段结构错误、重复完整模型名或套餐项非 object 都使目录快照无效，返回 502，detail 指出字段路径、期望和实际；不缓存、不回退。
- REQ-07：`exhausted === true` 表示耗尽；false/null 表示未耗尽。`enabled === false` 表示禁用；true/null/缺失按启用。
- REQ-08：每次请求按完整模型名 exact match；完全未出现才走普通 `.env`。
- REQ-09：出现模型但 `apiNames` 合法且所有项都不含 `messages` 返回 400；字段缺失/类型错误返回 502。
- REQ-10：支持 messages 但无有效套餐返回 503，detail 区分过期、耗尽、禁用、key 缺失或无可用套餐。
- REQ-11：多个有效套餐按 `expireAt` 降序选择；相同时间保持目录返回顺序；不比较或去重 key/id。
- REQ-12：命中套餐时请求体 model 保持原值；`x-api-key` 与 `Authorization` 使用 `apiKey.full`，增加 `X-Ai-Forward-Url=https://llm.api.zyuncs.com/v1`、`X-Pkg-Model=String(package.id)`，合法 mail 时增加 `X-Ai-Forward-Email`；不携带普通 key。
- REQ-13：未命中套餐时保持现有普通 headers，不增加智企 headers；实际连接地址始终为 `{CLAUDE_BASE_URL}/v1/messages`。
- REQ-14：每次请求使用不可变请求级 route；流式和非流式使用同一 route，不能修改全局 client 配置。
- REQ-15：缓存 TTL 为 1800 秒；每次请求读取 auth.json 并以 host/access_token 指纹检测变化；变化立即刷新。刷新 single-flight，旧认证结果不得覆盖新缓存；刷新失败清除旧快照。
- REQ-16：目录网络错误/超时单次 10 秒，最多重试一次，退避约 200ms；401、非 JSON、契约错误和其他 HTTP 错误不重试。
- REQ-17：auth 文件错误按 500/401；目录上游网络/响应错误按 502；协议不支持 400；套餐业务不可用 503。错误只返回详细 `detail`，敏感字段不回显。

## Scenarios

- SCN-01 Given 目录成功且模型完全未出现，When 请求模型，Then 使用普通 base URL/key 和现有 headers。
- SCN-02 Given 模型 exact match 且存在有效 messages 套餐，When 请求非流式或流式 completion，Then 保留 model 原值并使用套餐 key、三个转发头和既有上下文头。
- SCN-03 Given auth 指纹未变且缓存未过期，When 并发请求到达，Then 只发生一个目录刷新/复用同一快照。
- SCN-04 Given expireAt 等于或早于 now，When 请求模型，Then 返回 503 且说明套餐过期。
- SCN-05 Given模型存在但所有 apiNames 不含 messages，When 请求模型，Then 返回 400，不回退普通路由。
- SCN-06 Given模型出现但相关路由字段缺失/类型错误或重复，When 请求任意模型，Then 返回 502，指出完整路径，不缓存。
- SCN-07 Given auth.json 缺失/解析失败/host 非法/access_token 缺失，When 请求模型，Then 返回对应 500/401，不回退。
- SCN-08 Given目录请求网络失败，When 重试一次仍失败，Then 清除旧快照并返回 502；401 不重试。
- SCN-09 Given套餐列表为空或合法套餐 models 为空，When 请求未知模型，Then 缓存空目录并使用普通路由。
- SCN-10 Given mail 缺失或不符合 aiproxy 规则，When 命中套餐，Then 省略 X-Ai-Forward-Email，其他套餐请求继续。
- SCN-11 Given多个有效套餐，When expireAt 不同/相同，Then 分别选更晚者/返回顺序第一者。
- SCN-12 Given并发普通与套餐请求，When 同时发出，Then两者不串用 key、URL 或 headers。

## 关键决策

- 目录是所有模型路由的前置依赖；目录失败时普通模型也报错。
- 只改本 proxy 的 Chat Completions -> Claude Messages 链路。
- 目录字段只严格校验路由字段，非路由展示字段不做硬依赖。
- 任何路由字段契约错误使整个快照无效；合法空数组允许。
- 只以 `apiNames` 中的 `messages` 判断当前协议能力；不把 id 当数字解释。
- 错误只提供详细 detail，不新增 code 字段。

## 实施决策

新增独立的目录客户端/快照与路由解析模块；Claude client 接受请求级 route，endpoint 在转换请求后解析 route，再把 route 传入非流式/流式调用。缓存、HTTP transport、时钟和 auth 文件路径使用可注入依赖，生产默认值固定。

## 错误行为与恢复

刷新成功后完整快照原子替换。刷新失败、响应非法或契约校验失败清除旧快照；不使用 stale cache。目录网络错误仅重试一次。流建立前的路由错误返回 FastAPI JSON；流建立后沿用现有 SSE 错误格式。

## 兼容性

普通模型在目录明确未声明时保持原有 key/base URL/header 行为。命中套餐只改变请求级认证和转发头，仍连接原 `CLAUDE_BASE_URL`。不改变外部 Chat Completions/内部 Messages 协议。

## 测试决策

测试使用公开 HTTP endpoint 和注入的 HTTP transport/auth reader/clock，验证完整外部行为，不测试私有实现细节。保留现有 `tests/test_api.py`、`tests/test_client.py` 风格，新增目录/路由行为测试及端到端 header 测试。

## Test Seams

- `POST /v1/chat/completions`：覆盖普通/套餐、流式/非流式、状态码和响应错误。
- model-packages HTTP transport seam：覆盖鉴权 URL、响应 schema、重试和缓存。
- auth reader seam：覆盖 auth.json 解析、指纹变化和敏感字段处理。
- Claude upstream HTTP transport seam：验证最终发送 headers、URL 和 body。

## 范围之外

Codex Responses、aiproxy 代码、kwoo-client 代码、直连智企最终地址、模型别名映射、环境变量覆盖 TTL、错误 code 字段。

## 补充说明

错误 detail 不得包含 access_token、apiKey.full、Authorization 或 x-api-key 原文。`X-Ai-Forward-Email` 规则为非空、长度不超过 320、无 CR/LF、包含 `@`。
