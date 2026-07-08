# Auto Stop Fix: Dynamic Max Tokens Design

## 目标 (Goal)
解决客户端未传递 `max_tokens` 时，代理服务默认使用 4096 导致 Claude 模型（如 Claude 4.8 Opus、Claude Fable 5 等最新模型，支持高达 128k output tokens）输出长文本时被强制截断（“自动停止”）的问题。

## 架构与数据流 (Architecture & Data Flow)
借鉴 `claude-code-proxy` 的做法，引入基于模型的动态 Token 上限映射。

1. **核心映射 (Mapping)**：
   在 `src/core/constants.py` 中增加字典 `MODEL_MAX_TOKENS_MAP`，记录最新主流模型及其对应的最大输出 token 数：
   - 最新旗舰/前沿模型 (`claude-fable-5`, `claude-opus-4-8`, `claude-sonnet-5` 等)：128000 (128k)
   - Sonnet 4.6 与 Haiku 4.5：64000 (64k)
   - 其他近沿模型 (`claude-3-5-sonnet-20241022`, `claude-3-5-sonnet-20240620` 等)：8192
   - 老模型 (`claude-3-opus-20240229`, `claude-3-haiku-20240307` 等)：4096

2. **逻辑判断 (Logic in `resolve_max_tokens`)**：
   当请求到达 `src/conversion/request_converter.py` 的 `resolve_max_tokens` 时：
   - 优先使用客户端请求中显式带有的 `max_tokens` 或 `max_completion_tokens`。
   - 如果客户端没传，则获取 `request.model`。
   - 在 `MODEL_MAX_TOKENS_MAP` 中查找对应模型。
     - 如果命中，则使用该模型对应的最大 Token 上限。
     - 如果未命中（未知模型），则 fallback 到一个全局的安全默认值（可设为 8192 或 4096）。

## 边界情况处理 (Edge Cases)
- 客户端传了一个很小的值：遵从客户端的设定。
- 客户端传来一个超过上游允许的值：交给上游 API 返回 400 Bad Request，代理不干预硬性拦截。
- `request.model` 字段为空：由于 Pydantic 校验，通常不会为空，若极特殊情况发生， fallback 到默认值。

## 测试要求
- 修改代码后，无需重启上游即可验证：不传 `max_tokens` 且请求 `claude-fable-5` 或 `claude-opus-4-8` 时，生成的 Claude 格式请求中 `max_tokens` 应该被赋值为 128000。
