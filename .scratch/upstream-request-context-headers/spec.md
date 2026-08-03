Status: ready-for-agent

# 上游请求上下文 Header 补全与保留

## Problem Statement

用户通过 CC Switch 调用本地 Claude OpenAI Proxy 时，请求会先以 OpenAI Chat Completions 协议进入代理，再由代理转换为 Anthropic Messages 请求并发送到 aiproxy。当前代理在创建上游请求时会重新构造 Header，只携带内容类型、Anthropic 版本、上游鉴权信息和代理内部 request ID，不会保留客户端已经提供的 `X-Client-Task-Id` 与 `X-Client-Trace-Id`，也不会声明请求来自 IDE。

这会导致两个用户可见问题：第一，aiproxy 无法获得 `X-Src: ide`，同一把 API Key 可能被 SSO 按非 IDE 流量判定为额度耗尽并返回 401；第二，客户端已经建立的任务归属和链路追踪信息会在代理边界中断，无法跨 CC Switch、Claude OpenAI Proxy、aiproxy 和上游模型服务关联排障。

## Solution

Claude OpenAI Proxy 在每次向上游发送 Anthropic Messages 请求时固定携带 `X-Src: ide`，并为 `X-Client-Task-Id` 与 `X-Client-Trace-Id` 提供“优先保留、缺失补全”的行为：

- 入站请求已经携带非空的 `X-Client-Task-Id` 时，上游请求保留该值，不重新生成。
- 入站请求已经携带非空的 `X-Client-Trace-Id` 时，上游请求保留该值，不重新生成。
- 任一字段缺失或为空时，仅为该字段生成新的 ID。
- 两个 ID 使用相同的生成算法，但必须分别生成，不能共享同一个值。
- 生成格式与 WisCode 正常请求一致：使用无连字符的 UUID 表示，即 32 位小写十六进制字符串。
- 流式和非流式调用遵守完全相同的规则。

这样，IDE 请求能够按正确来源进入 aiproxy 的鉴权和额度判断，同时尽可能保持调用方已经建立的任务与追踪上下文。

## User Stories

1. As an IDE user, I want requests sent through Claude OpenAI Proxy to be identified as IDE traffic, so that aiproxy applies the correct authentication and quota policy.
2. As a CC Switch user, I want my usable WisCode API key to remain usable through the local conversion proxy, so that I do not receive a misleading quota-exhausted 401 caused by missing request context.
3. As a WisCode user, I want an existing client task ID to survive the proxy boundary, so that one user turn remains associated with the same task across services.
4. As a WisCode user, I want an existing client trace ID to survive the proxy boundary, so that one trace can be followed from the desktop client to aiproxy.
5. As a client without a task ID, I want the proxy to generate one automatically, so that the upstream request still has usable task correlation metadata.
6. As a client without a trace ID, I want the proxy to generate one automatically, so that the upstream request can still be located in diagnostic logs.
7. As a client that supplies only a task ID, I want that task ID preserved and only the trace ID generated, so that the proxy does not replace valid caller context.
8. As a client that supplies only a trace ID, I want that trace ID preserved and only the task ID generated, so that the proxy fills only the missing context.
9. As an operator, I want generated task and trace IDs to use the same predictable UUID format, so that logs and integrations can validate and search them consistently.
10. As an operator, I want generated task and trace IDs to be different values, so that task ownership and trace correlation remain distinct concepts.
11. As an operator, I want IDs generated independently for concurrent requests, so that unrelated requests cannot be accidentally correlated.
12. As an operator, I want streaming and non-streaming calls to carry the same context Headers, so that observability does not depend on response mode.
13. As an operator, I want the same resolved IDs to be reused throughout one outbound request lifecycle, so that retries within that lifecycle do not create contradictory metadata.
14. As a security-conscious operator, I want only the three approved context Headers added or preserved, so that unrelated inbound Headers are not leaked upstream.
15. As a security-conscious operator, I want upstream authentication Headers to remain controlled by proxy configuration, so that inbound client credentials cannot overwrite upstream channel credentials.
16. As a maintainer, I want Header names to be handled case-insensitively, so that standards-compliant clients work regardless of Header casing.
17. As a maintainer, I want empty or whitespace-only context Header values treated as missing, so that invalid empty metadata is not forwarded as if it were valid.
18. As a maintainer, I want context resolution implemented once and reused by both streaming and non-streaming paths, so that the two paths cannot drift.
19. As a maintainer, I want tests to observe the actual outbound HTTP request, so that passing tests prove the gateway receives the required Headers rather than merely proving an internal helper returns a dictionary.
20. As a maintainer, I want existing Chat Completions request and response conversion behavior to remain unchanged, so that the fix is limited to outbound request context.
21. As a maintainer, I want existing upstream error propagation and resource cleanup behavior to remain unchanged, so that Header support does not regress timeout or streaming handling.
22. As a support engineer, I want task and trace IDs to remain searchable across the client, proxy, and aiproxy, so that a reported request can be diagnosed without guessing which log entries belong together.

## Implementation Decisions

- The feature applies to every upstream Anthropic Messages request produced by the OpenAI Chat Completions endpoint, including streaming and non-streaming requests.
- `X-Src` is set to the fixed value `ide` by Claude OpenAI Proxy. This proxy instance is treated as a dedicated IDE-facing conversion service.
- The proxy reads `X-Client-Task-Id` and `X-Client-Trace-Id` from the inbound HTTP request using case-insensitive Header lookup.
- A present, non-empty caller value is authoritative and is forwarded unchanged. The proxy must not replace it with a locally generated value.
- A missing, empty, or whitespace-only value is replaced with a newly generated value for that field only.
- Both generated IDs use the same algorithm: a random UUID rendered without hyphens, producing a 32-character lowercase hexadecimal value.
- Task ID and trace ID generation are two independent operations. Even when both are missing, they must not receive the same generated value.
- Request-context resolution is a single responsibility owned by one shared component or function and is invoked before either the streaming or non-streaming upstream send path.
- The resolved request context is passed explicitly into the upstream client. The upstream client remains responsible for constructing the final upstream Header collection.
- The final Header merge order guarantees that the resolved `X-Src`, task ID, and trace ID are present while preserving proxy-controlled content type, Anthropic version, configured API key, authorization, and internal request ID behavior.
- The implementation is an allow-listed context transfer, not transparent forwarding of the complete inbound Header collection.
- Inbound `Authorization`, `X-Api-Key`, `Host`, `Content-Length`, connection-level Headers, cookies, and unrelated client Headers remain excluded from context passthrough.
- Existing internal `x-request-id` generation is not replaced by either client task ID or client trace ID.
- No persistence or database schema is required. Generated context exists only for the lifetime of the request.
- No new public endpoint or request-body field is introduced; this is an additive HTTP Header contract.

## Testing Decisions

- Use one highest-level seam: exercise the real FastAPI Chat Completions endpoint with the existing test client while an HTTP mock transport captures the actual outbound Anthropic Messages request.
- Tests assert externally observable outbound Header behavior rather than calling a Header-building helper directly.
- Parameterize the seam across streaming and non-streaming requests so both production paths are proven to obey the same contract.
- Verify that an inbound `X-Client-Task-Id` and `X-Client-Trace-Id` are preserved exactly in the outbound request.
- Verify that `X-Src` is always present with the value `ide`, regardless of whether caller context IDs are present.
- Verify that when both context IDs are missing, both are generated, each matches the 32-character lowercase hexadecimal UUID format, and the two values differ.
- Verify partial input in both directions: preserve task/generate trace and generate task/preserve trace.
- Verify empty and whitespace-only values are treated as missing and replaced with valid generated IDs.
- Verify Header lookup is case-insensitive by supplying noncanonical casing at the HTTP boundary.
- Verify unrelated inbound Headers are not copied into the outbound request.
- Verify inbound authentication values cannot overwrite configured upstream authentication values.
- Reuse the repository's existing FastAPI endpoint tests for request-level setup and its existing HTTP mock transport pattern for capturing outbound requests.
- Retain existing tests for request conversion, response conversion, upstream error handling, streaming preflight failures, stream cleanup, and cancellation as regression coverage.
- A good test must fail against the current implementation because at least `X-Src`, task ID, or trace ID is absent from the captured outbound request, and pass only when the actual upstream request contains the specified values.

## Out of Scope

- Forwarding every inbound HTTP Header unchanged.
- Changing API Key validation, API Key storage, quota calculation, or SSO behavior.
- Generating or validating `X-Ide-Token`.
- Forwarding `X-Ide-Token`, `X-Repo-Url`, `X-Model-Type`, `X-Client-Quota-Task-Id`, or `X-Client-Provider` as part of this change.
- Adding dynamic client-source detection for Claude Code, plugin, mobile, or other callers.
- Changing the fixed IDE source into a runtime configuration option.
- Changing OpenAI-to-Anthropic request conversion or Anthropic-to-OpenAI response conversion.
- Changing aiproxy, CC Switch, WisCode, or external SSO code.
- Introducing distributed tracing infrastructure or a new telemetry backend.
- Guaranteeing that a locally generated task ID groups multiple independent inbound HTTP requests into one higher-level user turn; without caller-provided context, generation is request-scoped.

## Further Notes

- The production failure was reproduced with the same API key and model: absence of `X-Src` produced an upstream quota-exhausted 401, while adding only `X-Src: ide` produced a successful response.
- WisCode normally generates task and trace IDs in the frontend using independent UUID generation and removes hyphens before sending them. This specification intentionally matches that format.
- In normal WisCode traffic, a caller-provided task or trace ID is more semantically accurate than a proxy-generated fallback because it can cover the complete user turn. Preservation therefore has priority over generation.
- `X-Client-Task-Id` represents task or turn ownership, while `X-Client-Trace-Id` represents diagnostic correlation. They may often share the same lifetime but remain distinct identifiers.
- The fixed `X-Src: ide` decision is appropriate only because this proxy is currently scoped as an IDE-facing compatibility layer. Supporting heterogeneous client sources later requires a separate specification.
