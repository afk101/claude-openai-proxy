"""Responses 请求的最小路由信封。"""

import json
from dataclasses import dataclass
from typing import Any, List, NoReturn, Tuple

from fastapi import HTTPException

from src.core.constants import Constants


class _JSONObjectPairs(list):
    """标记 JSON object 的原始键值对，供顶层重复键检查使用。"""


@dataclass(frozen=True)
class ResponsesEnvelope:
    """代理路由只需要读取的 model 与 stream 字段。"""

    model: str
    stream: bool


def _reject_non_standard_json_number(value: str) -> NoReturn:
    """拒绝 Python 解码器默认接受、但 JSON 标准不允许的非有限数值。"""
    raise ValueError(f"non-standard JSON number: {value}")


def parse_responses_envelope(raw_body: bytes) -> ResponsesEnvelope:
    """从原始 body 旁路提取路由字段，不重建将要发送给上游的请求。

    object_pairs_hook 会保留 JSON object 的重复键。代理只拒绝会影响本地
    路由决定的顶层 model/stream 重复项，其余字段及嵌套结构完全交给上游。
    """
    try:
        parsed = json.loads(
            raw_body,
            object_pairs_hook=_JSONObjectPairs,
            parse_constant=_reject_non_standard_json_number,
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=Constants.RESPONSES_INVALID_JSON_DETAIL,
        ) from exc

    if not isinstance(parsed, _JSONObjectPairs):
        raise HTTPException(
            status_code=400,
            detail=Constants.RESPONSES_INVALID_JSON_DETAIL,
        )

    pairs: List[Tuple[str, Any]] = parsed
    model_values = [value for key, value in pairs if key == "model"]
    stream_values = [value for key, value in pairs if key == "stream"]
    if len(model_values) > 1:
        raise HTTPException(
            status_code=400,
            detail=Constants.RESPONSES_DUPLICATE_MODEL_DETAIL,
        )
    if len(stream_values) > 1:
        raise HTTPException(
            status_code=400,
            detail=Constants.RESPONSES_DUPLICATE_STREAM_DETAIL,
        )

    model = model_values[0] if model_values else None
    if not isinstance(model, str) or not model.strip():
        raise HTTPException(
            status_code=400,
            detail=Constants.RESPONSES_INVALID_MODEL_DETAIL,
        )

    stream = stream_values[0] if stream_values else False
    if not isinstance(stream, bool):
        raise HTTPException(
            status_code=400,
            detail=Constants.RESPONSES_INVALID_STREAM_DETAIL,
        )
    return ResponsesEnvelope(model=model, stream=stream)
