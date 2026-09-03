# 01 — expand：新增 Responses 非流式透明入口

**构建内容：** 在旧 Chat/Claude 路径仍可运行的过渡状态下，新增一个可实际调用的 `POST /v1/responses` 非流式纵切片。调用方能够通过代理鉴权，以原始请求字节调用普通密钥上游，并收到原始状态、压缩 body 和重复响应头。

**受阻于：** 无——可以立即开始。

## 覆盖范围

- Requirements：`REQ-01`、`REQ-03`、`REQ-04`、`REQ-05`、`REQ-06`、`REQ-15`、`REQ-16`、`REQ-17`、`REQ-18`、`REQ-19`、`REQ-20`、`REQ-21`、`REQ-22`、`REQ-23`、`REQ-25`、`REQ-27`、`REQ-28`、`REQ-34`、`REQ-35`。
- Scenarios：`SCN-01`、`SCN-07`、`SCN-08`、`SCN-09`、`SCN-10`、`SCN-11`、`SCN-12`、`SCN-13`、`SCN-14`、`SCN-15`、`SCN-21`、`SCN-22`、`SCN-23`。
- 本 slice 只承诺目录完全未命中后的普通密钥非流式路径；套餐 Responses 选择由 Issue 02 完成。

## Acceptance Criteria

- [ ] 合法非流式请求通过公开 Responses 入口到达 `/v1/responses` 上游，捕获的 body bytes 与调用方发送值完全相同。
- [ ] 代理只旁路读取 model/stream；非法或重复路由字段在目录与模型上游之前返回 400。
- [ ] 目录完全未命中时使用普通密钥，且调用方凭据、Cookie、Host、Content-Length 和无关 Header 不进入上游。
- [ ] task/trace ID 的保留、独立生成和请求内复用对新入口生效。
- [ ] 缺失或非法 Base URL、缺失普通密钥在模型上游前返回脱敏 500；当前合法值只拼接一次 `/v1/responses`。
- [ ] 调用方缺失/空 Accept-Encoding 时上游收到 identity；非空或重复值按列表语义转发。
- [ ] 非流式成功与完整上游错误保留 status、raw body、gzip Content-Encoding、未知端到端 Header 和重复字段。
- [ ] 静态逐跳头、Connection 动态声明头、Content-Length、Server、Date 不被转发。
- [ ] 非流式 body 完整读取前连接中断返回 502，超时返回 504，资源只关闭一次且活动请求记录清零。
- [ ] 旧 Chat/Claude 入口和既有测试在 expand 阶段仍保持可运行。

## 验证方式

- 使用 `$tdd`，先写公开入口红灯测试，再实现最小纵切片。
- 运行 Responses 信封、客户端和公开 API 的聚焦测试文件。
- 使用直接 ASGI driver 验证 gzip raw bytes、重复 Header 和动态逐跳过滤。
- 使用 gated raw byte stream 验证非流式读取异常与幂等关闭。
- 运行完整现有 Python suite，证明 expand 没有破坏旧入口。

## 执行约束

- 新 Responses client 与旧 Claude client 暂时并存；不得在本 issue 提前删除旧 symbol。
- 新入口只可使用原始 bytes 发请求，不得通过 JSON 对象重新序列化。
- 非流式也必须以手动 streaming 模式打开上游并聚合 raw iterator。
- 响应重复 Header 必须经 ASGI raw header 列表交付，不能用普通 mapping 折叠。
- 新增固定值进入常量 module；新增函数职责单一并带详细中文注释。
- 日志只记录模型、请求 ID、流式标志、路由类型、状态和字节数，不记录 body 或凭据。

## 范围之外

- 智企目录中的 Responses 套餐选择。
- `stream=true` 的 SSE 透传。
- 删除旧 Chat/Claude 实现。
- 用户文档和最终真实上游矩阵。
