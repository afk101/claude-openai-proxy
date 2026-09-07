"""WisCode 本地认证文件的共享读取与校验。"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import HTTPException


_HOST_PATTERN = re.compile(r"^[A-Za-z0-9.-]+(?::[0-9]{1,5})?$")


@dataclass(frozen=True)
class WisCodeAuth:
    """经过校验、可安全用于 WisCode 上游请求的认证信息。"""

    host: str
    access_token: str
    mail: Optional[str]


class WisCodeAuthProvider:
    """集中读取认证文件，保证所有 WisCode 来源使用同一套校验规则。"""

    def __init__(self, auth_path: Optional[Path] = None) -> None:
        self.auth_path = auth_path or Path.home() / ".wiscode" / "auth.json"

    def read(self) -> WisCodeAuth:
        """读取认证文件，只返回请求所需字段，不向错误信息暴露凭据。"""
        try:
            raw = json.loads(self.auth_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=500,
                detail="无法读取 ~/.wiscode/auth.json：文件不存在",
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail="无法读取 ~/.wiscode/auth.json：文件读取失败",
            ) from exc
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=500,
                detail="~/.wiscode/auth.json JSON 解析失败",
            ) from exc
        if not isinstance(raw, dict):
            raise HTTPException(
                status_code=500,
                detail="~/.wiscode/auth.json 顶层类型错误：期望 object",
            )

        host = raw.get("host")
        if (
            not isinstance(host, str)
            or not host.strip()
            or not _HOST_PATTERN.fullmatch(host.strip())
        ):
            raise HTTPException(
                status_code=500,
                detail=(
                    "~/.wiscode/auth.json.host 字段非法："
                    "期望 hostname 或 hostname:port"
                ),
            )
        token = raw.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise HTTPException(
                status_code=401,
                detail="~/.wiscode/auth.json.access_token 缺失或无效",
            )
        mail = raw.get("mail")
        return WisCodeAuth(
            host.strip(),
            token.strip(),
            mail if isinstance(mail, str) else None,
        )

    @staticmethod
    def fingerprint(auth: WisCodeAuth) -> str:
        """生成不含明文 token 的指纹，用于隔离不同账号的缓存。"""
        raw = f"{auth.host}\0{auth.access_token}".encode()
        return hashlib.sha256(raw).hexdigest()
