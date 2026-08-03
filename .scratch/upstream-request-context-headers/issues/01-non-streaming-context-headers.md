# 01 — 非流式请求补全并保留上游上下文 Header

**What to build:** IDE 用户通过非流式 Chat Completions 调用代理时，上游请求固定声明 IDE 来源；客户端已有的任务与追踪 ID 原样保留，缺失的 ID 使用相同 UUID 算法分别补全。

**Blocked by:** None — can start immediately

**Status:** resolved

- [x] 非流式请求的真实上游 HTTP Header 始终包含 `X-Src: ide`。
- [x] 已有的非空 Task ID 与 Trace ID 原样保留，不被代理覆盖。
- [x] 缺失、空或仅空白的 ID 分别生成 32 位小写十六进制值，且两个生成值不同。
- [x] 只补全缺失字段，不复制无关入站 Header，也不允许入站鉴权覆盖上游鉴权。
