# Codex 自定义模型上下文限制未生效排查

## 用户提供的第一手信息

- 使用 `CODEX_HOME=/Users/qihoo/.codex-api` 的 Codex 实例。
- 该实例通过 CC Switch 路由请求供应商 `claude-openai-chat`，模型为 `360-glm-5.2`。
- 请求 `/responses` 时，上游返回 HTTP 400：
  - 模型最大上下文：`262144`
  - 输入消息：`143143`
  - 请求完成额度：`128000`
  - 请求总量：`271143`
- 用户预期 `cc-switch-model-catalog.json` 中的上下文约束能够阻止该请求超过模型上限。

## 当前调查目标

1. 确认 `/Users/qihoo/.codex-api/config.toml` 是否实际加载了正确的模型目录。
2. 确认模型目录中 `360-glm-5.2` 的 `context_window`、自动压缩阈值等字段和值。
3. 追踪请求中的 `128000` 是由 Codex、CC Switch 还是下游转换代理设置。
4. 明确模型目录的约束语义：影响 Codex 上下文压缩、输出额度，还是仅影响模型展示元数据。

## 已知历史背景

- 非默认 `CODEX_HOME` 使用相对 `model_catalog_json` 时，目录文件必须存在于对应 Home 下；仅存在默认 Home 的文件不足以让其他 Home 加载。
- 历史排查确认 `/Users/qihoo/.codex-api/config.toml` 可以使用相对的 `model_catalog_json`，但必须同时验证实际文件及其 JSON schema。

## 初始假设

- 假设 A：`/Users/qihoo/.codex-api` 没有加载到目标模型目录，导致 Codex 使用了其他上下文窗口。
- 假设 B：模型目录已加载，但目录声明的上下文窗口和上游真实限制不一致。
- 假设 C：目录正确，但 `128000` 是路由后的 Claude/OpenAI 转换层独立设置的输出额度，Codex 的目录不会替下游自动裁剪该值。

## 调查状态

- Phase 1：进行中。

## 调查记录

### 2026-07-27：配置与目录文件

- `/Users/qihoo/.codex-api/config.toml` 明确设置：
  - `model_provider = "custom"`
  - `model = "360-glm-5.2"`
  - `model_catalog_json = "cc-switch-model-catalog.json"`
- `/Users/qihoo/.codex-api/cc-switch-model-catalog.json` 实际存在，更新时间为 2026-07-27 09:54，包含 8 个模型。
- 因此假设 A 中“文件缺失或 config.toml 没有引用目录”的情况已排除。
- `/Users/qihoo/.codex-api/cc-switch-model-catalog.json` 是普通文件，不是到默认 Home 的软链接；它和 `/Users/qihoo/.codex/cc-switch-model-catalog.json` 大小、更新时间均不同。后续需要确认这是按 Home 写入的预期结果，还是生成内容不同步。
- 第一次日志搜索同时覆盖了大体积 body dump，命中历史请求正文并导致输出截断，没有拿到本次请求证据。该方法不再重复，后续改为先按精确 request id 定位文件，再读取最小上下文。

### 2026-07-27：模型声明与失败请求

- `360-glm-5.2` 在 `/Users/qihoo/.codex-api/cc-switch-model-catalog.json` 中声明：
  - `context_window = 200000`
  - `max_context_window = 200000`
  - `effective_context_window_percent = 95`
  - 未声明 `max_output_tokens`
- 按目录字段计算，Codex 的有效输入上下文阈值约为 `200000 × 95% = 190000` token。
- 上游报告本次输入为 `143143` token，低于 `190000`。因此即使模型目录已正确加载，Codex 也没有达到自动压缩阈值。
- 精确日志证实转换代理收到 189 条消息，并明确向上游发送 `max_tokens=128000`。
- 失败发生在上游 `code.jizhi.360.cn/aiproxy/v1/messages`，上游按 `143143 + 128000 = 271143` 计算，超过真实上限 `262144`，返回 HTTP 400。
- 这表明当前冲突不是“catalog 完全没加载”，而是 catalog 只按 `200000/190000` 管理输入上下文；另一层独立设置了 `128000` 的最大输出额度，二者没有共同遵守 `input + output <= 262144`。
- 第二次目录字段查看误带了体积很大的 `model_messages`，信息冗余但不影响结论；后续只读取必要字段，不再输出该对象。

## 当前假设收敛

- 假设 A 已排除：目录已被配置引用且文件存在。
- 假设 B 部分成立：目录使用 `200000` 作为 Codex 输入窗口，而上游使用 `262144` 作为输入与最大输出的总预算，双方语义不同。
- 假设 C 待最后确认：日志已经证明 `128000` 来自转换代理发送值，下一步反查其配置源和 CC Switch 请求是否原本携带该字段。

### 2026-07-27：`128000` 的直接来源

- `/Users/qihoo/Documents/A_Own/claude-openai-proxy/src/conversion/request_converter.py` 的 `resolve_max_tokens()` 逻辑为：
  1. 优先使用调用方 `max_tokens`；
  2. 其次使用调用方 `max_completion_tokens`；
  3. 两者都没有时，使用 `Constants.DEFAULT_MAX_TOKENS`。
- `/Users/qihoo/Documents/A_Own/claude-openai-proxy/src/core/constants.py` 将 `DEFAULT_MAX_TOKENS` 固定为 `128000`。
- 因此日志中的 `max_tokens=128000` 至少可确认是该转换代理的默认值候选；下一步只需检查 CC Switch 从 Codex Responses 转成 Chat Completions 后，是否携带了 `max_tokens` 或 `max_completion_tokens`。
- CC Switch 的 `/responses` 会根据供应商格式进入 `handle_codex_chat_to_responses_transform()`，即 Codex Responses 请求会被转换成上游 Chat Completions 请求后发往 `127.0.0.1:7072`。
- CC Switch 开启了本日 body dump，`/Users/qihoo/.cc-switch/logs/proxy-bodies/b341359e-437d-452c-a7ee-1019d99ae939/` 下存在本次时间段的请求记录，可以直接验证转换前后字段，不需要修改代码增加日志。

### 2026-07-27：转换前后字段验证

- `responses_to_chat_completions_with_reasoning()` 只在 Codex 请求显式提供以下字段时才复制：
  - `max_output_tokens` → `max_tokens` 或 `max_completion_tokens`
  - `max_tokens`
  - `max_completion_tokens`
- 检查 17:30 之后所有 `360-glm-5.2` body dump：
  - Client → CC Switch 请求有 `model`，没有上述三个额度字段。
  - CC Switch → Upstream 请求也没有上述三个额度字段。
- 因此 CC Switch 没有生成 `128000`，也没有把 catalog 的上下文值转换成输出额度。
- `claude-openai-proxy` 收到一个不含额度字段的 Chat Completions 请求后，`resolve_max_tokens()` 命中 `DEFAULT_MAX_TOKENS = 128000`。假设 C 已确认。
- Body dump 中 Client → CC Switch 请求带有由 catalog 提供的完整 Codex instructions，进一步证明该 Codex 实例已经实际加载了自定义 catalog，而不只是 `config.toml` 写了路径。

## 根因链（当前证据）

1. Codex 已加载 `360-glm-5.2` 的 catalog 条目。
2. 条目声明 `context_window=200000`、有效比例 `95%`，所以 Codex 在输入约达到 `190000` 前不会触发压缩。
3. 本次上游估算输入为 `143143`，未达到 Codex 压缩阈值。
4. Codex 请求未指定最大输出额度，CC Switch 转换时保持“未指定”。
5. `claude-openai-proxy` 把“未指定”补成 `128000`。
6. 上游按输入与最大输出的总和校验：`143143 + 128000 = 271143 > 262144`，拒绝请求。

## 模式与历史对比

- `claude-openai-proxy` 的历史提交 `4680028` 曾为解决“默认输出太小导致自动停止”，把未指定额度的请求按模型映射到较大的最大输出值。
- 提交 `f939b9d` 又把最终兜底值从 `200000` 调整为 `128000`。当前 `360-glm-5.2` 没有独立模型映射，所以直接命中该兜底值。
- 该兜底值是“单次最大输出 token”，不是“总上下文窗口”；代码没有接收输入 token 估算或模型总窗口，因此无法动态计算剩余输出空间。
- CC Switch catalog 生成器会把模型配置的 `contextWindow` 同时写入 `context_window` 与 `max_context_window`，但不会写 `max_output_tokens`。模板的 `effective_context_window_percent=95` 会被保留。
- 这与现场文件完全一致：模型配置的 `200000` 成功写入目录，目录约束工作在 Codex 的输入压缩边界；转换代理的 `128000` 工作在 Claude Messages 的输出上限，两者属于不同配置层。

## 最小验证

- 在 `claude-openai-proxy` 的实际虚拟环境中直接构造 `360-glm-5.2` Chat Completions 请求：
  - 不提供 `max_tokens`/`max_completion_tokens`：`resolve_max_tokens()` 返回 `128000`。
  - 显式提供 `max_tokens=8192`：返回 `8192`。
- 现有测试 `test_resolve_max_tokens_uses_generic_default_for_arbitrary_model` 也固定断言未知/自定义模型的默认值为 `128000`。
- 最小验证与生产日志一致，根因假设得到运行证据确认。

## 最终根本原因

`cc-switch-model-catalog.json` 已成功加载，也按配置生效；问题不是目录失效，而是两个独立预算没有联动：

- Codex 根据 catalog 的 `context_window=200000` 和 `effective_context_window_percent=95` 管理输入压缩，当前 `143143` 输入未达到约 `190000` 的压缩阈值。
- Codex 没有在 `/responses` 请求里指定最大输出额度，CC Switch 只做字段转换，不自行补值。
- `claude-openai-proxy` 对未指定额度的请求无条件补 `128000`，但不知道当前输入 token，也不知道该模型真实总窗口 `262144`。
- 最终上游看到 `143143 + 128000 > 262144`，所以在真正生成前返回 HTTP 400。

## 调查状态

- Phase 1：完成。
- Phase 2：完成。
- Phase 3：完成。
- Phase 4：等待用户确认根本原因后进入 brainstorming。

## Brainstorming 阶段补充调研

### 模型公开能力上限

- OpenAI GPT-5.6 Sol、Terra、Luna 与 GPT-5.5 的公开规格均为约 1.05M 总上下文、128K 最大输出。
- Anthropic Claude Opus 4.8 的公开规格为 1M 总上下文、128K 最大输出；Anthropic 对高努力等级的示例建议从 64K 输出预算开始调优，而不是默认使用能力上限。
- DeepSeek V4 Flash 官方规格为 1M 总上下文、最高 384K 输出。
- GLM-5.2 的公开资料为 1M 上下文、约 128K 最大输出，但当前实际经过的 `code.jizhi.360.cn` 网关明确按 262144 总上下文校验，因此代理必须以实际网关约束为准，不能照搬原厂模型上限。

### `max_tokens` 是否可省略

- Codex Responses 请求可以不写 `max_output_tokens`；当前现场请求确实没有写。
- CC Switch 转换后的 Chat Completions 请求也可以不写 `max_tokens`；当前现场请求同样没有写。
- `claude-openai-proxy` 最终调用的是 Claude Messages `/v1/messages`。Claude Messages 协议要求请求提供 `max_tokens`，因此最终一跳不能按标准协议直接省略。
- 如果直接从最终 Claude 请求删除该字段，只能依赖某个私有网关的非标准默认行为，兼容性不可控，不作为推荐方案。

## 技术决策

| 决策 | 理由 |
|------|------|
| 将 `claude-openai-proxy` 的通用默认 `max_tokens` 从 `128000` 调整为 `64000` | 当前 Codex 压缩阈值约为 `190000`，加上 `64000` 后为 `254000`，低于网关 `262144` 总限制并保留 `8144` token 缓冲 |
| 保留调用方显式 `max_tokens`/`max_completion_tokens` 的最高优先级 | 调用方了解具体任务和模型能力时，应允许覆盖通用默认值 |
| 不修改 CC Switch catalog 的 `context_window=200000` | catalog 已正确加载并生效，本次故障来自转换代理输出默认值过大 |
| 不直接省略最终 Claude Messages 请求的 `max_tokens` | 该字段是 Claude Messages 标准协议必填字段 |
| 本次仅修改默认常量和对应回归测试 | 目标明确，避免引入 token 估算器或模型映射等额外范围 |

## 实施验证记录

- 转换层测试先以“实际 `128000`、期望 `64000`”失败，修改常量后通过。
- 第一次完整测试发现 API 层仍有旧默认值断言，结果为 `1 failed, 29 passed`。
- 调整执行方案后，临时恢复旧常量并将 API 层期望改为 `64000`，确认 API 测试同样先失败；再恢复新常量后通过。
- 最终完整测试：`30 passed, 1 warning`。警告是既有 Starlette/httpx2 弃用提示。
- `python -m compileall src tests` 通过。

## 资源

- OpenAI GPT-5.6 Sol 模型规格：https://developers.openai.com/api/docs/models/gpt-5.6-sol
- Anthropic Claude 模型规格：https://platform.claude.com/docs/en/about-claude/models/overview
- Anthropic Messages API：https://platform.claude.com/docs/en/api/csharp/messages/create
