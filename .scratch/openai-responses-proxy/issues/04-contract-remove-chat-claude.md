# 04 — contract：删除旧 Chat/Claude 产品路径

**构建内容：** 在 Responses 非流式和流式路径已经稳定后，移除旧 Chat Completions 入口及失去调用者的 Claude 转换实现，让该分支只剩一个清晰的 Responses 产品模型。

**受阻于：** 03 — Responses 原生流式透传与取消清理。

## 覆盖范围

- Requirements：`REQ-01`、`REQ-02`、`REQ-29`、`REQ-32`、`REQ-33`、`REQ-34`、`REQ-35`。
- Scenarios：`SCN-24`、`SCN-26`、`SCN-32`。
- 本 slice 执行 expand–contract 的 contract 阶段，并保持所有新测试为绿。

## Acceptance Criteria

- [ ] `/v1/chat/completions` 返回 404，且只有 `POST /v1/responses` 是公开推理入口。
- [ ] Chat Completions ↔ Claude Messages 转换器、Claude 数据模型、旧流错误生成器和专属测试不再存在。
- [ ] 旧 Claude client 和 `/v1/messages` 上游路径不再可达。
- [ ] route resolver 的临时协议参数被收窄，最终只处理 Responses 能力。
- [ ] anthropic-version、ANTHROPIC_VERSION、Chat stop reason 映射和 `[DONE]` 合成退出运行契约。
- [ ] 删除仅限对应能力已经退出的代码；仍有用途的解释性注释不被误删。
- [ ] 删除后完整 suite 仍通过，且敏感信息扫描无新增问题。

## 验证方式

- 使用 `$tdd` 先固定旧 HTTP 路径 404 和新 Responses 回归，再执行 contract 删除。
- 运行引用搜索，确认没有可达 Chat/Claude conversion、Claude client 或 `/v1/messages`。
- 运行完整 Python suite 和编译检查。
- 检查最终 diff 中删除的注释只属于已经删除的旧功能。

## 执行约束

- 只能在新 Responses 测试已覆盖相同行为后删除旧测试。
- 不保留两套互相矛盾的运行模式或隐藏 feature flag。
- 保留与协议无关的目录复制脚本、请求上下文和启动脚本。
- 新增或移动的固定值仍必须位于常量 module。

## 范围之外

- README、包名、CLI help 和启动脚本扩展。
- 真实模型验收。
- 其他仓库的调用方迁移。
