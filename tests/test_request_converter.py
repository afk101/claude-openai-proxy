"""OpenAI system 消息到 Claude 顶层 system 的转换测试。"""

from src.conversion.request_converter import convert_openai_to_claude_request
from src.models.openai import OpenAIChatCompletionRequest


def test_convert_system_messages_to_single_text_content_block():
    """多条 system 消息应按顺序合并为单个 Claude text 内容块。"""
    request = OpenAIChatCompletionRequest(
        model="360-glm-5.2",
        messages=[
            {"role": "system", "content": "第一条规则"},
            {
                "role": "system",
                "content": [{"type": "text", "text": "第二条规则"}],
            },
            {"role": "user", "content": "你好"},
        ],
    )

    result = convert_openai_to_claude_request(request)

    assert result["system"] == [
        {
            "type": "text",
            "text": "第一条规则\n\n第二条规则",
        }
    ]


def test_omit_system_when_normalized_content_is_empty():
    """空 system 不应生成顶层字段或空内容块。"""
    empty_system_values = [None, "", []]

    for content in empty_system_values:
        request = OpenAIChatCompletionRequest(
            model="360-glm-5.2",
            messages=[
                {"role": "system", "content": content},
                {"role": "user", "content": "你好"},
            ],
        )

        result = convert_openai_to_claude_request(request)

        assert "system" not in result


def test_omit_system_when_request_has_no_system_message():
    """没有 system 消息时应保持顶层 system 缺失。"""
    request = OpenAIChatCompletionRequest(
        model="360-glm-5.2",
        messages=[{"role": "user", "content": "你好"}],
    )

    result = convert_openai_to_claude_request(request)

    assert "system" not in result
