#!/usr/bin/env python3
"""通过本地代理执行真实 OpenAI Responses 验收。"""

import argparse
import base64
import json
import os
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit, urlunsplit

import httpx

from scripts.live_verification_constants import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_PROXY_BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    FILE_CONTENT_MARKER,
    FILE_DOCUMENT_TEXT,
    FILE_NAME,
    FUNCTION_ARGUMENT_NAME,
    FUNCTION_ARGUMENT_VALUE,
    FUNCTION_FINAL_MARKER,
    FUNCTION_OUTPUT,
    FUNCTION_TOOL_NAME,
    MESSAGE_MATRIX_MODEL,
    MODEL_MATRIX,
    PROXY_BASE_URL_ENV,
    RED_PNG_DATA_URL,
    RESULT_TEXT_LIMIT,
    ROUTE_LOG_PATTERN,
    SENSITIVE_OUTPUT_PATTERNS,
    STREAM_FINAL_MARKER,
)
from src.core.constants import Constants


@dataclass(frozen=True)
class ResponseAssessment:
    """保存一次响应的脱敏判定结果，避免把完整上游错误写入输出。"""

    passed: bool
    response_status: Optional[str]
    error: Optional[str]
    text: str


@dataclass(frozen=True)
class SseEvent:
    """保存一个按 SSE 空行边界聚合后的事件名称和 data 文本。"""

    event: Optional[str]
    data: str


@dataclass(frozen=True)
class MessageCase:
    """描述一个必须通过真实上游的消息输入变体和预期输出标记。"""

    name: str
    marker: str
    payload: Dict[str, Any]


@dataclass(frozen=True)
class FunctionCall:
    """保存第一阶段模型实际产生的 function call 关键字段。"""

    call_id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class LiveResult:
    """保存一次真实请求的脱敏结果，字段足以证明终态但不包含完整响应。"""

    group: str
    case: str
    model: str
    route: str
    http_status: Optional[int]
    response_status: Optional[str]
    error: Optional[str]
    text: str
    passed: bool


def extract_output_text(payload: Mapping[str, Any]) -> str:
    """只从标准 message/output_text item 收集最终文本，不解释其他输出类型。"""
    text_parts = []
    output = payload.get("output")
    if not isinstance(output, list):
        return ""
    for output_item in output:
        if not isinstance(output_item, Mapping):
            continue
        content = output_item.get("content")
        if not isinstance(content, list):
            continue
        for content_item in content:
            if not isinstance(content_item, Mapping):
                continue
            if content_item.get("type") != "output_text":
                continue
            text = content_item.get("text")
            if isinstance(text, str) and text:
                text_parts.append(text)
    return "\n".join(text_parts)


def assess_completed_response(
    http_status: int,
    payload: Any,
    expected_text: str,
    *,
    require_explicit_error: bool = True,
) -> ResponseAssessment:
    """按 HTTP、Responses 终态、error 与语义标记的顺序判定完成响应。"""
    if not isinstance(payload, Mapping):
        return ResponseAssessment(False, None, "invalid_response_body", "")

    response_status = payload.get("status")
    normalized_status = response_status if isinstance(response_status, str) else None
    text = extract_output_text(payload)
    if not 200 <= http_status < 300:
        return ResponseAssessment(
            False,
            normalized_status,
            f"http_status_{http_status}",
            text,
        )
    if normalized_status != "completed":
        return ResponseAssessment(
            False,
            normalized_status,
            f"status_{normalized_status or 'missing'}",
            text,
        )

    if require_explicit_error and "error" not in payload:
        return ResponseAssessment(
            False,
            normalized_status,
            "error_field_missing",
            text,
        )
    upstream_error = payload.get("error")
    if upstream_error is not None:
        error_type = "present"
        if isinstance(upstream_error, Mapping):
            candidate = upstream_error.get("type") or upstream_error.get("code")
            if isinstance(candidate, str) and candidate:
                error_type = candidate[:80]
        return ResponseAssessment(
            False,
            normalized_status,
            f"upstream_error:{error_type}",
            text,
        )
    if expected_text not in text:
        return ResponseAssessment(
            False,
            normalized_status,
            "expected_text_missing",
            text,
        )
    return ResponseAssessment(True, normalized_status, None, text)


def parse_sse_events(raw_stream: bytes) -> Tuple[SseEvent, ...]:
    """按 SSE 规则聚合 event 与多行 data，忽略注释和未知字段。"""
    text = raw_stream.decode("utf-8")
    events: List[SseEvent] = []
    event_name: Optional[str] = None
    data_lines: List[str] = []

    def flush_event() -> None:
        """只在当前块包含协议字段时追加事件，空 keepalive 块不产生假事件。"""
        nonlocal event_name, data_lines
        if event_name is not None or data_lines:
            events.append(SseEvent(event_name, "\n".join(data_lines)))
        event_name = None
        data_lines = []

    for line in text.splitlines():
        if not line:
            flush_event()
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)
    flush_event()
    return tuple(events)


def assess_stream_response(
    http_status: int,
    raw_stream: bytes,
    expected_text: str,
) -> ResponseAssessment:
    """要求原生流包含可解析的 response.completed，且拒绝旧 `[DONE]` 终止符。"""
    if not 200 <= http_status < 300:
        return ResponseAssessment(False, None, f"http_status_{http_status}", "")
    try:
        events = parse_sse_events(raw_stream)
    except UnicodeDecodeError:
        return ResponseAssessment(False, None, "invalid_sse_utf8", "")

    if any(event.data.strip() == "[DONE]" for event in events):
        return ResponseAssessment(False, None, "synthetic_done_event", "")

    for event in reversed(events):
        if event.event != "response.completed":
            continue
        try:
            event_payload = json.loads(event.data)
        except json.JSONDecodeError:
            return ResponseAssessment(False, None, "invalid_completed_event", "")
        if not isinstance(event_payload, Mapping):
            return ResponseAssessment(False, None, "invalid_completed_event", "")
        response_payload = event_payload.get("response")
        # 真实智企 SSE 的 completed response 会省略 nullable error 字段。
        # 透明代理不能补写字段；这里仍会拒绝任何实际存在的非空 error。
        return assess_completed_response(
            http_status,
            response_payload,
            expected_text,
            require_explicit_error=False,
        )

    return ResponseAssessment(False, None, "missing_response_completed", "")


def _build_request_payload(model: str, input_value: Any) -> Dict[str, Any]:
    """统一构造小输出上限的请求，避免各消息用例产生无关配置差异。"""
    return {
        "model": model,
        "input": input_value,
        "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
    }


def build_pdf_file_data_url(text: str) -> str:
    """生成包含固定 ASCII 标记的最小单页 PDF data URL。"""
    # PDF 文字串中的反斜杠和圆括号具有语法含义，必须先转义。
    escaped_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content_stream = (
        f"BT /F1 14 Tf 72 720 Td ({escaped_text}) Tj ET\n".encode("ascii")
    )
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        (
            b"<< /Length "
            + str(len(content_stream)).encode("ascii")
            + b" >>\nstream\n"
            + content_stream
            + b"endstream"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    )

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for object_number, pdf_object in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{object_number} 0 obj\n".encode("ascii"))
        pdf.extend(pdf_object)
        pdf.extend(b"\nendobj\n")

    # xref 的字节偏移使上游 PDF 解析器可以稳定读取这个自包含文件。
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    encoded_pdf = base64.b64encode(pdf).decode("ascii")
    return f"data:application/pdf;base64,{encoded_pdf}"


def build_message_cases(model: str) -> Tuple[MessageCase, ...]:
    """构造 spec 批准的字符串、四角色、两 phase 与三 content 用例。"""
    cases = [
        MessageCase(
            "string_input",
            "STRING INPUT OK",
            _build_request_payload(model, "只回复 STRING INPUT OK"),
        ),
        MessageCase(
            "role_user",
            "USER ROLE OK",
            _build_request_payload(
                model,
                [
                    {
                        "type": "message",
                        "role": "user",
                        "content": "只回复 USER ROLE OK",
                    }
                ],
            ),
        ),
        MessageCase(
            "role_assistant",
            "ASSISTANT ROLE OK",
            _build_request_payload(
                model,
                [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": "这是先前的助手消息。",
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": "只回复 ASSISTANT ROLE OK",
                    },
                ],
            ),
        ),
        MessageCase(
            "role_system",
            "SYSTEM ROLE OK",
            _build_request_payload(
                model,
                [
                    {
                        "type": "message",
                        "role": "system",
                        "content": "必须只回复 SYSTEM ROLE OK",
                    },
                    {"type": "message", "role": "user", "content": "按要求回答"},
                ],
            ),
        ),
        MessageCase(
            "role_developer",
            "585",
            _build_request_payload(
                model,
                [
                    {
                        "type": "message",
                        "role": "developer",
                        "content": "回答算术问题时只输出阿拉伯数字结果，不添加说明。",
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": "计算 314 + 271。",
                    },
                ],
            ),
        ),
        MessageCase(
            "phase_commentary",
            "COMMENTARY PHASE OK",
            _build_request_payload(
                model,
                [
                    {
                        "type": "message",
                        "role": "assistant",
                        "phase": "commentary",
                        "content": "这是先前的 commentary 阶段消息。",
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": "只回复 COMMENTARY PHASE OK",
                    },
                ],
            ),
        ),
        MessageCase(
            "phase_final_answer",
            "FINAL ANSWER PHASE OK",
            _build_request_payload(
                model,
                [
                    {
                        "type": "message",
                        "role": "assistant",
                        "phase": "final_answer",
                        "content": "这是先前的 final_answer 阶段消息。",
                    },
                    {
                        "type": "message",
                        "role": "user",
                        "content": "只回复 FINAL ANSWER PHASE OK",
                    },
                ],
            ),
        ),
        MessageCase(
            "content_input_text",
            "INPUT TEXT OK",
            _build_request_payload(
                model,
                [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "只回复 INPUT TEXT OK"}
                        ],
                    }
                ],
            ),
        ),
        MessageCase(
            "content_input_image",
            "RED IMAGE OK",
            _build_request_payload(
                model,
                [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "如果图片是红色方块，只回复 RED IMAGE OK；否则回复 OTHER",
                            },
                            {
                                "type": "input_image",
                                "image_url": RED_PNG_DATA_URL,
                                "detail": "low",
                            },
                        ],
                    }
                ],
            ),
        ),
        MessageCase(
            "content_input_file",
            FILE_CONTENT_MARKER,
            _build_request_payload(
                model,
                [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "这是无害的本地集成测试 PDF。读取其中 "
                                    "'Verification code:' 后的值，只回复该值。"
                                ),
                            },
                            {
                                "type": "input_file",
                                "filename": FILE_NAME,
                                "file_data": build_pdf_file_data_url(
                                    FILE_DOCUMENT_TEXT
                                ),
                            },
                        ],
                    }
                ],
            ),
        ),
    ]
    return tuple(cases)


def _build_function_tools() -> List[Dict[str, Any]]:
    """构造唯一测试函数定义，使模型产生可确定参数的 function_call。"""
    return [
        {
            "type": "function",
            "name": FUNCTION_TOOL_NAME,
            "description": "Return the fixed verification value for one key.",
            "parameters": {
                "type": "object",
                "properties": {FUNCTION_ARGUMENT_NAME: {"type": "string"}},
                "required": [FUNCTION_ARGUMENT_NAME],
                "additionalProperties": False,
            },
        }
    ]


def build_function_request(model: str) -> Dict[str, Any]:
    """构造必须先调用函数、收到结果后再输出固定标记的第一阶段请求。"""
    payload = _build_request_payload(
        model,
        [
            {
                "type": "message",
                "role": "user",
                "content": (
                    f"调用 {FUNCTION_TOOL_NAME}，参数 {FUNCTION_ARGUMENT_NAME} "
                    f"必须是 {FUNCTION_ARGUMENT_VALUE}。"
                    f"得到工具结果后只回复 {FUNCTION_FINAL_MARKER}。"
                ),
            }
        ],
    )
    payload["tools"] = _build_function_tools()
    payload["tool_choice"] = {"type": "function", "name": FUNCTION_TOOL_NAME}
    return payload


def find_function_call(payload: Any, expected_name: str) -> Optional[FunctionCall]:
    """从真实第一阶段 output 中找到目标函数，并严格解析其 JSON object 参数。"""
    if not isinstance(payload, Mapping):
        return None
    output = payload.get("output")
    if not isinstance(output, list):
        return None
    for item in output:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") != "function_call" or item.get("name") != expected_name:
            continue
        call_id = item.get("call_id")
        arguments_text = item.get("arguments")
        if not isinstance(call_id, str) or not call_id:
            return None
        if not isinstance(arguments_text, str):
            return None
        try:
            arguments = json.loads(arguments_text)
        except json.JSONDecodeError:
            return None
        if not isinstance(arguments, Mapping):
            return None
        return FunctionCall(call_id, expected_name, arguments)
    return None


def build_function_continuation(
    model: str,
    original_input: Sequence[Mapping[str, Any]],
    first_output: Sequence[Mapping[str, Any]],
    function_call: Optional[FunctionCall],
) -> Dict[str, Any]:
    """把第一阶段原始 items 与匹配 call_id 的工具输出组成无状态第二阶段请求。"""
    if function_call is None:
        raise ValueError("missing function call")
    continuation_input = list(original_input) + list(first_output)
    continuation_input.append(
        {
            "type": "function_call_output",
            "call_id": function_call.call_id,
            "output": FUNCTION_OUTPUT,
        }
    )
    payload = _build_request_payload(model, continuation_input)
    payload["tools"] = _build_function_tools()
    return payload


def parse_route_log(log_path: Path) -> Dict[str, str]:
    """只从代理元数据日志提取模型和路由类型，忽略所有其他行与字段。"""
    routes: Dict[str, str] = {}
    try:
        with log_path.open(encoding="utf-8", errors="replace") as log_file:
            for line in log_file:
                match = re.search(ROUTE_LOG_PATTERN, line)
                if match is None:
                    continue
                routes[match.group("model")] = match.group("route")
    except OSError:
        return {}
    return routes


def _safe_text_summary(text: str) -> str:
    """压平、截断并遮盖常见 token 形态，避免模型或上游错误意外回显凭据。"""
    sanitized = text.replace("\r", " ").replace("\n", " ")
    for pattern in SENSITIVE_OUTPUT_PATTERNS:
        sanitized = re.sub(pattern, "[REDACTED]", sanitized)
    return sanitized[:RESULT_TEXT_LIMIT]


def _safe_upstream_error(payload: Any) -> Optional[str]:
    """只保留 Responses error 的 type/code，不输出可能包含凭据的 message。"""
    if not isinstance(payload, Mapping):
        return None
    error = payload.get("error")
    if error is None:
        return None
    if not isinstance(error, Mapping):
        return "upstream_error:present"
    candidate = error.get("type") or error.get("code")
    if not isinstance(candidate, str) or not candidate:
        return "upstream_error:present"
    normalized = re.sub(r"[^A-Za-z0-9_.:-]", "_", candidate[:80])
    return f"upstream_error:{normalized}"


def _decode_json_response(response: httpx.Response) -> Any:
    """解析完整非流式 JSON；失败时返回 None，绝不把原 body 写入日志或结果。"""
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _live_result_from_assessment(
    group: str,
    case: str,
    model: str,
    http_status: int,
    payload: Any,
    assessment: ResponseAssessment,
) -> LiveResult:
    """把离线判定映射为统一真实结果，并优先记录脱敏的上游 error 类型。"""
    return LiveResult(
        group=group,
        case=case,
        model=model,
        route="unknown",
        http_status=http_status,
        response_status=assessment.response_status,
        error=_safe_upstream_error(payload) or assessment.error,
        text=_safe_text_summary(assessment.text),
        passed=assessment.passed,
    )


def _transport_failure_result(
    group: str,
    case: str,
    model: str,
    exception: httpx.RequestError,
) -> LiveResult:
    """网络失败只记录异常类型，不记录可能携带 URL 或凭据的异常文本。"""
    return LiveResult(
        group=group,
        case=case,
        model=model,
        route="unknown",
        http_status=None,
        response_status=None,
        error=f"transport_error:{type(exception).__name__}",
        text="",
        passed=False,
    )


def _post_case(
    client: httpx.Client,
    url: str,
    group: str,
    case: str,
    model: str,
    payload: Mapping[str, Any],
    marker: str,
) -> Tuple[LiveResult, Any]:
    """发送一个非流式用例并返回脱敏结果及仅供后续判定的内存 JSON。"""
    try:
        response = client.post(
            url,
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
    except httpx.RequestError as exception:
        return _transport_failure_result(group, case, model, exception), None
    response_payload = _decode_json_response(response)
    assessment = assess_completed_response(response.status_code, response_payload, marker)
    return (
        _live_result_from_assessment(
            group,
            case,
            model,
            response.status_code,
            response_payload,
            assessment,
        ),
        response_payload,
    )


def run_model_matrix(client: httpx.Client, url: str) -> List[LiveResult]:
    """逐项请求图片中的 11 个模型，并保留每项完整判定而不因单项失败提前退出。"""
    results = []
    for model in MODEL_MATRIX:
        payload = _build_request_payload(model, "只回复 TEST OK")
        result, _ = _post_case(
            client,
            url,
            "models",
            model,
            model,
            payload,
            "TEST OK",
        )
        results.append(result)
    return results


def run_message_matrix(client: httpx.Client, url: str) -> List[LiveResult]:
    """使用用户指定模型逐项执行全部已批准消息角色、phase 和 content 变体。"""
    results = []
    for message_case in build_message_cases(MESSAGE_MATRIX_MODEL):
        result, _ = _post_case(
            client,
            url,
            "messages",
            message_case.name,
            MESSAGE_MATRIX_MODEL,
            message_case.payload,
            message_case.marker,
        )
        results.append(result)
    return results


def _assess_function_call_stage(http_status: int, payload: Any) -> ResponseAssessment:
    """验证第一阶段已完成、error 为空，并产生名称和参数都正确的函数调用。"""
    if not isinstance(payload, Mapping):
        return ResponseAssessment(False, None, "invalid_response_body", "")
    response_status = payload.get("status")
    normalized_status = response_status if isinstance(response_status, str) else None
    if not 200 <= http_status < 300:
        return ResponseAssessment(False, normalized_status, f"http_status_{http_status}", "")
    if normalized_status != "completed":
        return ResponseAssessment(
            False,
            normalized_status,
            f"status_{normalized_status or 'missing'}",
            "",
        )
    if "error" not in payload:
        return ResponseAssessment(False, normalized_status, "error_field_missing", "")
    if payload.get("error") is not None:
        return ResponseAssessment(False, normalized_status, "upstream_error:present", "")
    function_call = find_function_call(payload, FUNCTION_TOOL_NAME)
    if function_call is None:
        return ResponseAssessment(False, normalized_status, "missing_function_call", "")
    if (
        function_call.arguments.get(FUNCTION_ARGUMENT_NAME)
        != FUNCTION_ARGUMENT_VALUE
    ):
        return ResponseAssessment(False, normalized_status, "unexpected_function_arguments", "")
    return ResponseAssessment(True, normalized_status, None, "function_call verified")


def run_function_round_trip(client: httpx.Client, url: str) -> List[LiveResult]:
    """真实完成 function_call → function_call_output 两阶段，并验证最终固定文本。"""
    model = MESSAGE_MATRIX_MODEL
    first_request = build_function_request(model)
    try:
        first_response = client.post(
            url,
            content=json.dumps(first_request, ensure_ascii=False).encode("utf-8"),
        )
    except httpx.RequestError as exception:
        return [
            _transport_failure_result(
                "function",
                "function_call",
                model,
                exception,
            )
        ]
    first_payload = _decode_json_response(first_response)
    first_assessment = _assess_function_call_stage(
        first_response.status_code,
        first_payload,
    )
    first_result = _live_result_from_assessment(
        "function",
        "function_call",
        model,
        first_response.status_code,
        first_payload,
        first_assessment,
    )
    if not first_result.passed or not isinstance(first_payload, Mapping):
        return [first_result]

    function_call = find_function_call(first_payload, FUNCTION_TOOL_NAME)
    output = first_payload.get("output")
    if function_call is None or not isinstance(output, list):
        return [replace(first_result, passed=False, error="missing_function_call")]
    continuation = build_function_continuation(
        model,
        first_request["input"],
        output,
        function_call,
    )
    final_result, _ = _post_case(
        client,
        url,
        "function",
        "function_call_output",
        model,
        continuation,
        FUNCTION_FINAL_MARKER,
    )
    return [first_result, final_result]


def run_stream_case(client: httpx.Client, url: str) -> LiveResult:
    """执行真实原生流并以 response.completed、无非空 error 和文本标记判定。"""
    payload = _build_request_payload(MESSAGE_MATRIX_MODEL, "只回复 STREAM OK")
    payload["stream"] = True
    try:
        with client.stream(
            "POST",
            url,
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        ) as response:
            raw_stream = b"".join(response.iter_raw())
            http_status = response.status_code
    except httpx.RequestError as exception:
        return _transport_failure_result(
            "stream",
            "native_stream",
            MESSAGE_MATRIX_MODEL,
            exception,
        )
    assessment = assess_stream_response(
        http_status,
        raw_stream,
        STREAM_FINAL_MARKER,
    )
    return _live_result_from_assessment(
        "stream",
        "native_stream",
        MESSAGE_MATRIX_MODEL,
        http_status,
        None,
        assessment,
    )


def _build_proxy_url(base_url: str) -> str:
    """校验本地代理根地址并追加唯一的 Responses Create 路径。"""
    try:
        parsed = urlsplit(base_url)
        parsed.port
    except ValueError as exception:
        raise ValueError("invalid local proxy base URL") from exception
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid local proxy base URL")
    path = parsed.path.rstrip("/")
    if path.endswith(Constants.RESPONSES_CREATE_PATH) or path.endswith("/v1"):
        raise ValueError("local proxy base URL must end before /v1")
    normalized = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    return f"{normalized}{Constants.RESPONSES_CREATE_PATH}"


def _attach_routes(results: Sequence[LiveResult], routes: Mapping[str, str]) -> List[LiveResult]:
    """把代理日志中的实际 route_type 关联到每个同模型的真实结果。"""
    return [replace(result, route=routes.get(result.model, "unknown")) for result in results]


def _print_results(results: Sequence[LiveResult]) -> None:
    """逐行输出可机器读取的脱敏 JSON，并给出分组通过计数。"""
    for result in results:
        print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    groups = sorted({result.group for result in results})
    for group in groups:
        group_results = [result for result in results if result.group == group]
        passed = sum(result.passed for result in group_results)
        print(f"SUMMARY group={group} passed={passed} total={len(group_results)}")


def _parse_arguments(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """解析验收范围和本地连接参数；上游凭据不属于该工具的参数面。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("models", "messages", "all"),
        default="all",
        help="models 运行 11 模型记录；messages 运行完整消息矩阵；all 运行两者",
    )
    parser.add_argument(
        "--proxy-base-url",
        default=os.environ.get(PROXY_BASE_URL_ENV, DEFAULT_PROXY_BASE_URL),
        help=f"本地代理根地址，默认读取 {PROXY_BASE_URL_ENV}",
    )
    parser.add_argument(
        "--route-log",
        type=Path,
        help="可选代理日志路径，用于把脱敏 route_type 关联到结果",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="每次真实请求的超时秒数",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """运行所选真实矩阵；11 模型只要求逐项记录，消息矩阵任一失败即非零。"""
    arguments = _parse_arguments(argv)
    try:
        url = _build_proxy_url(arguments.proxy_base_url)
    except ValueError as exception:
        print(f"ERROR {exception}")
        return 2

    headers = {
        Constants.HEADER_CONTENT_TYPE: Constants.RESPONSES_CONTENT_TYPE,
        Constants.HEADER_ACCEPT_ENCODING: Constants.RESPONSES_DEFAULT_ACCEPT_ENCODING,
    }
    proxy_api_key = os.environ.get(Constants.ENV_PROXY_API_KEY)
    if proxy_api_key:
        headers["Authorization"] = f"Bearer {proxy_api_key}"

    results: List[LiveResult] = []
    timeout = httpx.Timeout(arguments.timeout)
    with httpx.Client(headers=headers, timeout=timeout, trust_env=False) as client:
        if arguments.mode in {"models", "all"}:
            results.extend(run_model_matrix(client, url))
        if arguments.mode in {"messages", "all"}:
            results.extend(run_message_matrix(client, url))
            results.extend(run_function_round_trip(client, url))
            results.append(run_stream_case(client, url))

    routes = parse_route_log(arguments.route_log) if arguments.route_log else {}
    results = _attach_routes(results, routes)
    _print_results(results)

    required_results = [result for result in results if result.group != "models"]
    return 0 if all(result.passed for result in required_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
