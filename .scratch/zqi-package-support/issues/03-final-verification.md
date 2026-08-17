# 03 — 完整验证与文档

**构建内容：** 通过完整测试、启动代理并用 curl 验证普通模型、套餐模型及错误响应，补齐用户可运行配置说明。

**受阻于：** 02 — 请求级 route 与 Claude 请求接入。

## 覆盖范围

REQ-01 至 REQ-17；SCN-01 至 SCN-12。

## Acceptance Criteria

- [ ] 完整 pytest 通过，新增测试覆盖 spec 的关键正常/错误路径。
- [ ] 本地服务启动成功，curl 可观察到普通/套餐请求路由和清晰错误。
- [ ] README/.env.example 不要求新增智企 token 配置，并说明 auth.json 读取规则。

## 验证方式

- `pytest`。
- 启动 uvicorn 后执行脱敏 curl；使用本地 mock catalog/upstream 记录 headers。
- 检查 git diff 和敏感信息扫描。

## 执行约束

- 不输出真实 access_token 或 apiKey.full。
- 真实网络验证只能记录状态码、字段和脱敏响应。

## 范围之外

不修改其他仓库，不增加 Codex Responses。
