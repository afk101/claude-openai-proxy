# Findings: 模型自动停止问题调查

## 1. 用户反馈
- **现象描述**：模型总是自动停止，需要用户手动发送“继续”才能输出完整内容。
- **环境/验证条件**：使用 `.env` 中提供的 `baseurl` 和 `key`。
- **目标**：排查是上游问题还是服务自身的问题，找到根因后再修改代码。

## 2. 调查过程
### 2.1 检查服务配置与代理实现
- 观察 `src/conversion/request_converter.py` 文件，发现解析 `max_tokens` 的逻辑：
  `return request.max_tokens or request.max_completion_tokens or Constants.DEFAULT_MAX_TOKENS`
- `Constants.DEFAULT_MAX_TOKENS` 在 `src/core/constants.py` 中被硬编码为 `4096`。

### 2.2 本地验证
- 在本地启动服务，并发送一个要求输出长文的请求（请求模型为 `claude-3-5-sonnet-20241022`）。
- 观察到服务确实截断了输出，返回体中包含：
  `"finish_reason":"length"` 和 `"completion_tokens":4096`。

### 2.3 参考镜像仓库实现
- 参照了 `/Users/qihoo/Documents/A_Own/claude-code-proxy`。
- 镜像仓库的做法是：在 `config.py` 中维护了一个 `MODEL_TOKENS_MAP` 的映射表。
- 镜像仓库请求时会判断：`"max_tokens": min(max(claude_request.max_tokens, config.min_tokens_limit), max_tokens_limit)`，即如果在 `auto_tokens_mode` 下，会针对不同模型给一个适配的、更大的 limit 限制，从而防止截断。

### 2.4 最新的 Claude 模型调研 (2026-07)
- 通过 Exa Search 查询了最新的 Claude 模型规格：
  - **Claude Opus 4.8 (`claude-opus-4-8`)**: 支持最大 **128,000 (128k)** output tokens。
  - **Claude Fable 5 (`claude-fable-5`)**: 支持最大 **128,000 (128k)** output tokens。
  - **Claude Sonnet 5 (`claude-sonnet-5`)**: 支持最大 **128,000 (128k)** output tokens。
  - **Claude Sonnet 4.6 / Opus 4.6**: 在批处理 API 支持到 300k，常规情况目前通常也可以设置到非常大的值。

## 3. 结论
根本原因已确认：代理服务 `src/conversion/request_converter.py` 中的 `resolve_max_tokens` 函数在缺少客户端传递时使用了写死的 4096 作为默认值，而最新的 Claude 模型能够输出且有时必须输出超过 4096 tokens 的内容（最新甚至支持 128k）。上游收到的 `max_tokens` 参数为 4096，因此生成被强行截断，表现出来的现象就是“自动停止”。

## 4. 技术决策
| 决策 | 理由 |
|------|------|
| 引入 `MODEL_MAX_TOKENS_MAP` | 旧的全局 4096 默认值已严重落后于当前模型（如 Opus 4.8 支持 128k 输出）。需要根据模型动态映射最大允许的 token 数量。 |

## 5. 遇到的问题
| 问题 | 解决方案 |
|------|---------|
| Claude 长文本自动停止 | 实现模型级别的 Token 上限映射。 |

## 6. 视觉 / 浏览器发现
无

---
*每执行 2 次查看/浏览器/搜索操作后更新此文件*