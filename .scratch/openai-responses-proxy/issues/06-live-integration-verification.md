# 06 — 最终集成与真实 Responses 消息类型闭环

**构建内容：** 在本地启动最终代理，通过真实上游重测全部候选模型，并使用指定的 `z-ai/glm-5.3-flash` 完成所有已定义 Responses 消息角色、内容类型、function call continuation 和流式交互；任何失败都继续修正并重跑，直到整组通过。

**受阻于：** 05 — 收口 start.sh、配置和用户可见文档。

## 覆盖范围

- Requirements：`REQ-28`、`REQ-35`、`REQ-36`、`REQ-37`、`REQ-38`、`REQ-39`、`REQ-40`、`REQ-41`。
- Scenarios：`SCN-27`、`SCN-28`、`SCN-29`、`SCN-30`、`SCN-31`、`SCN-32`。
- 本 slice 是最终 completion gate，不允许用 mock、直连或部分成功替代本地代理真实结果。

## Acceptance Criteria

- [ ] 完整 Python suite、编译检查、shell 测试和 CLI help 均通过。
- [ ] 本地服务由 `start.sh` 启动并监听 7072，所有真实请求首先进入本地 `/v1/responses`。
- [ ] 图片中的 11 个模型逐项完成脱敏记录，包括实际路由、HTTP、Responses status、error 和文本。
- [ ] `z-ai/glm-5.3-flash` 的字符串 input 以及 user、assistant、system、developer 四种 message role 均返回 2xx、completed、error null 和可验证文本。
- [ ] 同一模型的 assistant commentary/final_answer phase、input_text、微小自包含 input_image、微小自包含 input_file 均返回 2xx、completed、error null 和内容相关输出。
- [ ] 同一模型完成一次由模型产生 function_call、代理回送匹配 function_call_output、模型返回最终文本的两阶段交互。
- [ ] 同一模型完成原生 `stream=true` 请求，观察到 `response.completed`，且代理没有追加 `[DONE]` 或自定义事件。
- [ ] 任一真实消息变体失败时，不将其降级为“上游不支持”后跳过；必须定位代理字节、Header、路由或上游契约差异，修正后重新执行完整消息矩阵。
- [ ] 可重复验证工具不读取或输出上游密钥；运行结果和仓库敏感信息扫描均通过。

## 验证方式

- 使用 `$tdd` 为真实 verifier 的解析与判定逻辑先写离线测试，再运行网络验证。
- 执行 spec 中全部自动命令，并保存脱敏的通过矩阵。
- 运行 11 模型本地代理矩阵。
- 运行 `z-ai/glm-5.3-flash` 消息矩阵：string、四 role、两 phase、三 content、function call 两阶段、stream。
- 对失败项保存 HTTP/status/error/输出摘要和代理日志中的 request ID，按根因修复后重跑整组。
- 输出足以让 coordinator 以 findings 中唯一 Review Base Commit 为 fixed point 执行最终 code review 的验证证据。

## 执行约束

- 必须使用用户指定模型，不得替换成其他模型。
- 图片、文件等输入必须本地生成并以内嵌 data 形式发送，不依赖第三方 URL。
- 真实凭据只从现有忽略的 `.env`、WisCode auth 或进程环境读取，不能进入命令文本、输出、文件或 git 历史。
- HTTP 200 不是单独通过条件；每个非流式用例都必须检查 completed、error null 和语义输出。
- 对 11 模型矩阵，透明返回已知 WisGPT 402 可以证明代理保真，但不能替代指定 GLM 消息矩阵的全绿条件。
- 不 push；每个修正保持 issue 范围内并在完成后提交。

## 范围之外

- 将上游真正不支持的模态转换成文本或替换模型。
- 穷举所有 built-in tools、MCP、computer use、音频输出或全部 SSE event union。
- 发布、部署或修改其他仓库。
