# Codex 模型输出默认额度设计

## 背景

Codex 通过 CC Switch 将 `/responses` 转换为 OpenAI Chat Completions，再由
`claude-openai-proxy` 转换为 Claude Messages。Codex 和 CC Switch 均未在现场请求中
指定最大输出额度，转换代理因此使用通用默认值 `128000`。

当 `360-glm-5.2` 输入达到 `143143` token 时，上游按
`143143 + 128000 = 271143` 校验，超过该路由的 `262144` 总上下文限制并返回 HTTP 400。

## 目标

- 将转换代理未收到显式额度时的默认 `max_tokens` 调整为 `64000`。
- 保持调用方显式 `max_tokens` 和 `max_completion_tokens` 的覆盖行为不变。
- 不改变 CC Switch 模型目录、路由策略和协议转换行为。

## 非目标

- 不实现请求 token 精确计数。
- 不新增按模型配置的输出额度映射。
- 不修改上游网关或模型上下文限制。
- 不改变流式错误处理。

## 设计

修改 `claude-openai-proxy/src/core/constants.py` 中的
`Constants.DEFAULT_MAX_TOKENS`，从 `128000` 调整为 `64000`。

`resolve_max_tokens()` 的优先级保持不变：

1. 使用调用方 `max_tokens`。
2. 使用调用方 `max_completion_tokens`。
3. 两者均未提供时使用 `Constants.DEFAULT_MAX_TOKENS`。

按照当前 catalog 的 `context_window=200000` 与有效比例 `95%`，Codex 约在
`190000` token 触发压缩。默认输出调整为 `64000` 后，总预算约为 `254000`，
低于上游 `262144` 限制，保留约 `8144` token 缓冲。

## 错误处理

不新增错误分支。若调用方显式传入过大的输出额度，上游仍返回原始参数错误，
代理继续按现有错误映射返回给 Codex。

## 测试

- 先将现有通用默认值测试的期望从 `128000` 改为 `64000`，确认测试先失败。
- 修改常量后确认该测试通过。
- 确认显式 `max_tokens` 与 `max_completion_tokens` 覆盖测试继续通过。
- 运行代理完整 pytest 测试集和 `compileall`。

## 验收标准

- 未指定输出额度的 `360-glm-5.2` 请求被转换为 `max_tokens=64000`。
- 显式输出额度不被代理覆盖。
- 代理完整测试集通过。
- CC Switch catalog 文件和生成逻辑没有代码变更。
