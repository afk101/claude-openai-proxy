"""上游请求上下文解析。"""

import uuid
from dataclasses import dataclass
from typing import Mapping, Optional

from src.core.constants import Constants


@dataclass(frozen=True)
class UpstreamRequestContext:
    """发送给上游的任务与追踪上下文。"""

    client_task_id: str
    client_trace_id: str


def generate_client_context_id() -> str:
    """生成与 WisCode 格式一致的无连字符 UUID。"""
    return uuid.uuid4().hex


def resolve_request_context(headers: Mapping[str, str]) -> UpstreamRequestContext:
    """保留非空客户端上下文，并为缺失字段分别生成标识。"""
    task_id = _read_non_empty_header(headers, Constants.HEADER_CLIENT_TASK_ID)
    trace_id = _read_non_empty_header(headers, Constants.HEADER_CLIENT_TRACE_ID)
    return UpstreamRequestContext(
        client_task_id=task_id or generate_client_context_id(),
        client_trace_id=trace_id or generate_client_context_id(),
    )


def _read_non_empty_header(headers: Mapping[str, str], name: str) -> Optional[str]:
    """读取非空 Header，同时保留调用方提供的原始值。"""
    value = headers.get(name)
    if value is None or not value.strip():
        return None
    return value
