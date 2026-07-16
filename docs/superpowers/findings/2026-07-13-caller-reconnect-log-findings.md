# 调用方重连日志排查记录

## 用户报告

- 调用方发生重新连接后，怀疑代理服务报错。
- 需要检查 `/Users/qihoo/Documents/A_Own/sh/logs` 中的日志。

## 已确认范围

- 目标日志目录包含代理日志 `claude-openai-proxy.log`、主服务日志 `ccc-auto-d.log` 及多个滚动文件、启动日志和其他组件日志。
- 本次只进行只读排查，未修改服务、配置或日志。

## 待收集证据

- 重连时间附近的调用方错误和代理 HTTP 状态。
- 代理是否记录客户端断开、上游错误或 Python 堆栈。
- 同一时间的主服务重连记录与请求 ID。

## 首轮日志证据

- `claude-openai-proxy.log` 在最近时间段持续记录来自同一客户端端口 `127.0.0.1:51090` 的流式 Chat Completions 请求。
- 每个请求均记录上游 HTTP `200`，代理向调用方也记录 HTTP `200`，未在已读片段中出现 Python traceback、5xx、499 或 `chat_completion_client_disconnected`。
- 请求的 `message_count` 从 173 连续增加至 199，`tool_count` 固定为 24；每次流式响应仅有 3 个输出 chunk 即完成。这符合调用方在工具回合后不断把历史消息累积并再次发起请求的模式，尚不能证明代理主动报错。
- 主服务 `ccc-auto-d.log` 的最近修改时间早于代理日志，且初筛输出混入大量旧日志；下一步需针对代理错误关键字及 websocket 服务的重连时间做精确筛选。

## 根本原因确认

- 代理日志明确记录过上游流式请求返回 HTTP `400`：`模型 'gpt-5.5' 不支持`，并且当前流式实现会在响应已开始后抛出异常，导致 `RuntimeError: Caught handled exception, but response already started.`
- 用户已澄清该模型切换是主动操作，且真正要排查的重连发生在切回后正常执行阶段。因此上述错误是独立问题，**不作为本次正常重连的根因**。
- 在该异常之后，最新一段日志显示同一调用方持续重试；上游和下游均为 HTTP `200`。不过其 `message_count` 已从 173 增长到 212、工具数固定 24，说明重试循环持续累积历史，后续可能放大上下文和请求负担。
- `tuitui-ws-server.log` 另有 websocket 在 12:26:35 关闭后 10 秒重连的独立记录；它与本次 Chat Completions 流中断并非同一错误链路。

## 当前假设

- 正常执行阶段的重复请求可能是调用方业务循环，而非网络层重连：代理日志中客户端端口始终为 `51090`，且每一轮均完成 HTTP `200`。
- 需继续核对运行中的代理版本、每轮 SSE 的实际结束原因与调用方日志时间线，才能判断是响应语义触发重试还是业务端主动继续工具/代理循环。

## 运行态核对

- 监听 `7072` 的实际代理是 PID `5137`，工作目录为当前项目，启动时间为 11:23:22，且未使用 `--reload`。任何启动后的源码修改都不会自动加载，需在后续修复验证时明确重启该生产进程。
- 代理日志在正常阶段显示多条不同模型请求均有 `chat_completion_stream_finished`，而非 `chat_completion_client_disconnected`；上游与下游均记为 HTTP `200`。
- 现有日志没有每条代理事件的时间戳，也没有记录最终 SSE `finish_reason` / 内容块数，无法仅凭历史日志把“业务连续请求”与“调用方因异常重连”严格区分。

## 正常执行阶段的关键模式

- 代理共记录 246 次 `chat_completion_stream_started` 和 246 次 `chat_completion_stream_finished`，`chat_completion_client_disconnected` 为 0；说明这些请求并不是代理检测到客户端断开后取消的。
- 最后 39 次流均以 `chunk_count=3` 正常结束。按照当前转换器，这三个 chunk 正好是 assistant role 首块、最终结束块、`[DONE]`，即没有任何文本、推理或工具调用增量。
- 因此“正常阶段重连”的直接代理侧表现不是 HTTP/网络失败，而是收到一个空的、`stop` 型 SSE 完成。调用方若将空完成视为需要继续执行，就会再次发起请求，并使消息历史持续增长。
- 空完成由上游返回还是由已累积的调用方消息结构触发，历史日志无法区分；需要在下一次复现时关联最终 stop reason、上游事件类型和请求 ID。

## 启动方式与诊断计划

- `/Users/qihoo/Documents/A_Own/sh/start.sh` 会并行启动六个服务；代理对应第六项，实际命令为在项目目录激活 `.venv` 后执行 `uv run python -m src.main`，并将输出追加至 `claude-openai-proxy.log`。
- 为避免重复启动脚本内其他五项服务，后续仅以同等的第六项命令重启代理。
- 现有日志只能记录 stream 的总 chunk 数，无法识别其是否为“初始 role + 结束 + DONE”的空完成。将新增只含请求 ID、事件类型计数、转换内容/推理/工具块数、停止原因和 token 用量的摘要日志；不记录消息正文、工具参数或密钥。

## 新增诊断日志

- 已按测试先行新增“空流必须输出摘要”的用例：实现前失败，实现后与原有文本流、工具流测试一同通过（`3 passed`）。
- 摘要字段包括请求 ID、上游 SSE 事件数/类型、文本/推理/工具增量数、OpenAI 最终结束原因、上游停止原因、token 用量与流是否完成；不记录正文、工具参数或密钥。

## 重启与上线验证

- 全量验证完成：13 个测试通过，编译检查与 diff 检查通过。
- 代理已重启并监听 `7072`（PID `96078`）；健康检查返回 healthy。
- 使用真实流式 curl 验证新日志已写入 `/Users/qihoo/Documents/A_Own/sh/logs/claude-openai-proxy.log`。日志可见上游事件计数、文本和推理增量数、`finish_reason=stop`、`upstream_stop_reason=end_turn` 等摘要字段。
- 代理以 `start.sh` 第六项的同等命令运行，并保留同一日志重定向；未执行整个 `start.sh`，从而避免重复启动其余五个不相关服务。
