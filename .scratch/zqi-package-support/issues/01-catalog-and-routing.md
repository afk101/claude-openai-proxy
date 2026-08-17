# 01 — 目录客户端与套餐路由

**构建内容：** 代理能够从 `$HOME/.wiscode/auth.json` 查询并缓存智企目录，严格校验路由字段，并为任意完整模型名解析出普通或套餐 route。

**受阻于：** 无——可以立即开始。

## 覆盖范围

REQ-02 至 REQ-11、REQ-15 至 REQ-17；SCN-01、SCN-03 至 SCN-11。

## Acceptance Criteria

- [ ] auth、host、目录 URL、目录 schema、套餐/模型字段错误均返回明确 detail 且不泄露敏感值。
- [ ] 目录 30 分钟缓存、指纹变化刷新、single-flight、单次网络重试和旧缓存清理行为可观察。
- [ ] exact match、协议能力、状态过滤和多套餐选择遵循 spec。

## 验证方式

- 目录客户端和路由公开行为测试。
- `pytest tests/test_zqi_catalog.py`。

## 执行约束

- 不把 package.id 当数字校验或排序；只字符串化为 Header 值。
- 任意路由字段契约错误使整个快照失败；空数组合法。
- token/key 不写日志、不出现在 detail。

## 范围之外

Claude upstream 请求 headers 接入和 endpoint wiring 由后续 issue 完成。
