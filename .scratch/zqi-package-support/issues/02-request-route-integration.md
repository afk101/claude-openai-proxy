# 02 — 请求级 route 与 Claude 请求接入

**构建内容：** Chat Completions endpoint 在每个请求中解析独立 route，非流式和流式 Claude Messages 请求使用同一 route，并正确发送普通或智企 headers。

**受阻于：** 01 — 目录客户端与套餐路由。

## 覆盖范围

REQ-01、REQ-12 至 REQ-14；SCN-01、SCN-02、SCN-10、SCN-12。

## Acceptance Criteria

- [ ] 命中套餐时只使用套餐 key，保留原始 model，发送三个智企头和合法 mail 头，仍连接 CLAUDE_BASE_URL/v1/messages。
- [ ] 未命中时现有普通 headers 不变且不含智企头。
- [ ] 普通/套餐并发请求不串 route；流式/非流式均验证。

## 验证方式

- `pytest tests/test_api.py tests/test_zqi_integration.py`。
- 使用 MockTransport 断言上游 URL、body 和 headers。

## 执行约束

- 禁止修改全局 client 的 api_key/base_url 作为请求路由手段。
- 401/403 不触发目录刷新或模型重试。

## 范围之外

Codex Responses、aiproxy/kwoo-client 修改、目录客户端内部 schema 逻辑。
