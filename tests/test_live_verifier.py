"""真实 Responses 验收器的离线判定与请求构造测试。"""

import base64
import json

import pytest

from scripts.verify_live_responses import (
    assess_completed_response,
    assess_stream_response,
    build_function_continuation,
    build_function_request,
    build_message_cases,
    find_function_call,
    parse_route_log,
    parse_sse_events,
)


def _completed_payload(text="TEST OK"):
    """构造与真实 Responses 输出结构一致的最小完成响应。"""
    return {
        "status": "completed",
        "error": None,
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
    }


def test_completed_assessment_requires_all_success_signals():
    """HTTP、终态、error 和语义输出必须同时满足才可通过。"""
    success = assess_completed_response(200, _completed_payload(), "TEST OK")

    assert success.passed is True
    assert success.response_status == "completed"
    assert success.error is None
    assert success.text == "TEST OK"


@pytest.mark.parametrize(
    ("http_status", "payload", "expected_error"),
    [
        (429, _completed_payload(), "http_status_429"),
        (
            200,
            {**_completed_payload(), "status": "failed"},
            "status_failed",
        ),
        (
            200,
            {**_completed_payload(), "error": {"type": "server_error"}},
            "upstream_error:server_error",
        ),
        (
            200,
            {key: value for key, value in _completed_payload().items() if key != "error"},
            "error_field_missing",
        ),
        (200, _completed_payload("wrong text"), "expected_text_missing"),
    ],
)
def test_completed_assessment_rejects_partial_success(
    http_status, payload, expected_error
):
    """单独的 HTTP 200 或 completed 都不能掩盖其他失败信号。"""
    assessment = assess_completed_response(http_status, payload, "TEST OK")

    assert assessment.passed is False
    assert assessment.error == expected_error


def test_sse_parser_and_assessment_require_native_completed_event():
    """SSE 验收必须忽略未知事件，并从真实 completed 事件读取最终响应。"""
    raw_stream = (
        b": keepalive\n\n"
        b"event: response.future\n"
        b"data: {\"type\":\"response.future\"}\n\n"
        b"event: response.completed\n"
        b"data: {\"type\":\"response.completed\",\n"
        b"data: \"response\":{\"status\":\"completed\",\"error\":null,"
        b"\"output\":[{\"type\":\"message\",\"content\":[{\"type\":"
        b"\"output_text\",\"text\":\"STREAM OK\"}]}]}}\n\n"
    )

    events = parse_sse_events(raw_stream)
    assessment = assess_stream_response(200, raw_stream, "STREAM OK")

    assert [event.event for event in events] == [
        "response.future",
        "response.completed",
    ]
    assert assessment.passed is True
    assert assessment.response_status == "completed"
    assert assessment.error is None


def test_stream_assessment_accepts_completed_response_without_nullable_error():
    """透明流不补字段；上游省略 nullable error 时仍可由 completed 终态证明成功。"""
    raw_stream = (
        b"event: response.completed\n"
        b'data: {"type":"response.completed","response":'
        b'{"status":"completed","output":[{"type":"message","content":'
        b'[{"type":"output_text","text":"STREAM OK"}]}]}}\n\n'
    )

    assessment = assess_stream_response(200, raw_stream, "STREAM OK")

    assert assessment.passed is True
    assert assessment.response_status == "completed"
    assert assessment.error is None


@pytest.mark.parametrize(
    ("raw_stream", "expected_error"),
    [
        (b"data: [DONE]\n\n", "synthetic_done_event"),
        (
            b'event: response.output_text.delta\ndata: {"delta":"A"}\n\n',
            "missing_response_completed",
        ),
    ],
)
def test_stream_assessment_rejects_synthetic_or_incomplete_stream(
    raw_stream, expected_error
):
    """旧 Chat 终止符和没有 Responses 终态的截断流都不得通过。"""
    assessment = assess_stream_response(200, raw_stream, "STREAM OK")

    assert assessment.passed is False
    assert assessment.error == expected_error


def test_message_matrix_contains_every_approved_role_phase_and_content_type():
    """消息矩阵必须完整覆盖 spec 定义的 10 个稳定输入变体。"""
    cases = build_message_cases("z-ai/glm-5.3-flash")

    assert {case.name for case in cases} == {
        "string_input",
        "role_user",
        "role_assistant",
        "role_system",
        "role_developer",
        "phase_commentary",
        "phase_final_answer",
        "content_input_text",
        "content_input_image",
        "content_input_file",
    }
    assert all(case.payload["model"] == "z-ai/glm-5.3-flash" for case in cases)
    assert all(case.payload["max_output_tokens"] == 1024 for case in cases)
    assert all(case.marker for case in cases)
    json.dumps([case.payload for case in cases])

    payloads = {case.name: case.payload for case in cases}
    assert payloads["string_input"]["input"] == "只回复 STRING INPUT OK"
    assert payloads["role_user"]["input"][0]["role"] == "user"
    assert payloads["role_assistant"]["input"][0]["role"] == "assistant"
    assert payloads["role_system"]["input"][0]["role"] == "system"
    assert payloads["role_developer"]["input"][0]["role"] == "developer"
    assert payloads["phase_commentary"]["input"][0]["phase"] == "commentary"
    assert payloads["phase_final_answer"]["input"][0]["phase"] == "final_answer"

    image_content = payloads["content_input_image"]["input"][0]["content"]
    file_content = payloads["content_input_file"]["input"][0]["content"]
    assert image_content[1]["type"] == "input_image"
    assert image_content[1]["image_url"].startswith("data:image/png;base64,")
    assert file_content[1]["type"] == "input_file"
    assert file_content[1]["filename"] == "verification.pdf"
    assert file_content[1]["file_data"].startswith("data:application/pdf;base64,")
    encoded_pdf = file_content[1]["file_data"].partition(",")[2]
    decoded_pdf = base64.b64decode(encoded_pdf)
    assert decoded_pdf.startswith(b"%PDF-1.4\n")
    assert b"FILE CONTENT OK" in decoded_pdf
    assert b"startxref" in decoded_pdf


def test_function_continuation_reuses_model_output_and_matching_call_id():
    """第二阶段必须回送模型产生的 function_call 和完全一致的 call_id。"""
    first_request = build_function_request("z-ai/glm-5.3-flash")
    first_output = [
        {
            "type": "function_call",
            "name": "get_test_value",
            "call_id": "call_verified_123",
            "arguments": '{"key":"test"}',
        }
    ]

    function_call = find_function_call(
        {"status": "completed", "error": None, "output": first_output},
        "get_test_value",
    )
    continuation = build_function_continuation(
        "z-ai/glm-5.3-flash",
        first_request["input"],
        first_output,
        function_call,
    )

    assert function_call is not None
    assert function_call.call_id == "call_verified_123"
    assert function_call.arguments == {"key": "test"}
    assert continuation["input"][:1] == first_request["input"]
    assert continuation["input"][1] == first_output[0]
    assert continuation["input"][2] == {
        "type": "function_call_output",
        "call_id": "call_verified_123",
        "output": "TEST OK",
    }


def test_route_log_parser_uses_only_safe_model_and_route_fields(tmp_path):
    """路由记录只提取代理明确写出的模型与 package/ordinary，不接触凭据。"""
    log_path = tmp_path / "proxy.log"
    log_path.write_text(
        "INFO responses_completed request_id=req-1 model=model/a "
        "route_type=package status=200 bytes=10\n"
        "INFO responses_stream_started request_id=req-2 model=model/b "
        "route_type=ordinary status=200\n"
        "WARNING unrelated secret-like text must be ignored\n",
        encoding="utf-8",
    )

    assert parse_route_log(log_path) == {
        "model/a": "package",
        "model/b": "ordinary",
    }
