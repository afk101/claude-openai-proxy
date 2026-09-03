# 03 — Responses 原生流式透传与取消清理

**构建内容：** 让 `stream=true` 的调用方收到上游原生 SSE 字节，并在流式错误、ASGI 取消或客户端断开时停止上游工作和释放资源，不生成任何代理事件。

**受阻于：** 02 — 接入智企 Responses 套餐路由。

## 覆盖范围

- Requirements：`REQ-14`、`REQ-16`、`REQ-17`、`REQ-18`、`REQ-22`、`REQ-23`、`REQ-24`、`REQ-25`、`REQ-26`、`REQ-27`、`REQ-28`、`REQ-34`、`REQ-35`。
- Scenarios：`SCN-02`、`SCN-11`、`SCN-12`、`SCN-16`、`SCN-17`、`SCN-18`、`SCN-19`、`SCN-20`、`SCN-32`。
- 本 slice 交付套餐与普通密钥两类原生流式路径。

## Acceptance Criteria

- [x] SSE 的拼接 raw bytes 与上游一致，空行、注释、多行 data、未知事件和终止事件均保留。
- [x] 代理不追加 `[DONE]`，不生成 response.completed、response.failed 或自定义错误事件。
- [x] 流式 status、Content-Type、Content-Encoding、重复端到端 Header 和逐跳过滤遵循非流式相同契约。
- [x] 上游流前 4xx/5xx 以原 status/header/body 开始下游响应。
- [x] 下游开始后的上游异常只终止 body、留下脱敏日志并清理资源。
- [x] 直接取消 ASGI task 时，取消向连接、响应头等待或 raw body 读取传播，无需释放测试 gate。
- [x] `http.disconnect` 或下游 send 失败时，上游流停止，response/client/活动记录最多关闭一次。
- [x] 套餐与普通密钥流式路径使用相同上下文解析和安全 Header 规则。

## 验证方式

- 使用 `$tdd`，先通过 gated AsyncByteStream 和直接 ASGI driver 写流式红灯测试。
- 测试未知 SSE 事件、gzip SSE、流前错误、流中断、task cancel、http.disconnect 和 send failure。
- 运行流式客户端、公开 API 与资源生命周期聚焦测试。
- 运行完整 Python suite。

## 执行约束

- 必须读取 raw bytes，不得复用按行解码逻辑。
- 取消异常必须显式进入 finally 清理路径。
- 首版不增加独立的非流式全阶段断连 watcher。
- 流开始后不得把本地异常伪装成合法 Responses 消息。
- 新增日志必须同时脱敏普通 key、套餐 key 和目录 token。

## 范围之外

- 主动生成或解释任意 SSE event。
- 保证 HTTP chunk 分块位置一致。
- 旧路径删除和产品重命名。
