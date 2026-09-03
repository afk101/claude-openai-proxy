# 02 — 接入智企 Responses 套餐路由

**构建内容：** 让公开 Responses 入口在目录命中时选择支持 `responses` 的可用企业套餐，并在只有模型完全未出现时保留普通密钥回退。旧 Chat 入口在 expand 阶段继续按原 `messages` 能力运行。

**受阻于：** 01 — expand：新增 Responses 非流式透明入口。

## 覆盖范围

- Requirements：`REQ-07`、`REQ-08`、`REQ-09`、`REQ-10`、`REQ-11`、`REQ-12`、`REQ-13`、`REQ-14`、`REQ-15`、`REQ-16`、`REQ-17`、`REQ-28`、`REQ-34`、`REQ-35`。
- Scenarios：`SCN-01`、`SCN-02`、`SCN-03`、`SCN-04`、`SCN-05`、`SCN-06`、`SCN-07`、`SCN-10`、`SCN-11`。
- 本 slice 完成套餐/普通密钥决策树，但不实现原生 SSE。

## Acceptance Criteria

- [x] 模型名称以原值、区分大小写精确匹配，不做别名或裁剪。
- [x] Responses 入口只在 apiNames 含精确小写 responses 的候选中选择；仅 messages 或大小写错误时返回 400 且不回退。
- [x] 可用候选按内网、外网、未知 identifier 和同组目录顺序选择。
- [x] 过期、耗尽或禁用候选被跳过；全部不可用时返回 503 且不回退。
- [x] 目录完全未出现模型时使用普通密钥；目录/auth/JSON/schema 失败时沿用明确错误且不回退。
- [x] 套餐 key 覆盖普通 key，并携带 X-Ai-Forward-Url、X-Pkg-Model 和有效邮箱 Header。
- [x] 普通密钥路径不携带任何套餐 Header。
- [x] 旧 Chat 入口在 contract issue 前仍按 messages 协议通过原测试。

## 验证方式

- 使用 `$tdd` 先参数化目录测试数据，再增加 Responses-only、mixed-protocol、case mismatch 和全部不可用红灯场景。
- 运行 route resolver 聚焦测试。
- 通过公开 Responses 入口配合真实 resolver 测试替身，验证最终上游 key 和套餐 Header。
- 运行完整 Python suite，证明新旧入口在 expand 阶段均为绿色。

## 执行约束

- expand 阶段可让 resolver 接受内部协议参数；该临时兼容面不能暴露为新的 HTTP 参数。
- 保留现有 auth、目录严格校验、TTL、single-flight、认证变化刷新和失败清缓存语义。
- `responses` 必须定义为常量，不得散落字符串。
- 不能通过普通密钥绕开一个已经出现在目录中的模型。
- 不输出目录 access token、套餐 key 或普通 key。

## 范围之外

- 流式 Responses。
- 删除旧 messages 分支。
- 改变套餐优先级或根据 expireAt 排序。
- 为不支持 Responses 的套餐转换协议。
