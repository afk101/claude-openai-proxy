#!/bin/sh

set -eu

AUTH_FILE="${HOME}/.wiscode/auth.json"
QUERY="include_limit=true&include_keys=true&include_models=true"

fail() {
    printf '错误：%s\n' "$1" >&2
    exit 1
}

command -v curl >/dev/null 2>&1 || fail "找不到 curl"
command -v pbcopy >/dev/null 2>&1 || fail "找不到 pbcopy；该脚本需要在 macOS 上运行"
command -v python3 >/dev/null 2>&1 || fail "找不到 python3"
[ -f "$AUTH_FILE" ] || fail "找不到 ${AUTH_FILE}"

AUTH_VALUES=$(python3 - "$AUTH_FILE" <<'PY'
import json
import re
import sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as auth_file:
        auth = json.load(auth_file)
except FileNotFoundError:
    print("auth.json 文件不存在", file=sys.stderr)
    raise SystemExit(1)
except OSError:
    print("auth.json 无法读取", file=sys.stderr)
    raise SystemExit(1)
except json.JSONDecodeError:
    print("auth.json JSON 解析失败", file=sys.stderr)
    raise SystemExit(1)

if not isinstance(auth, dict):
    print("auth.json 顶层类型错误：期望 object", file=sys.stderr)
    raise SystemExit(1)

host = auth.get("host")
token = auth.get("access_token")
if not isinstance(host, str) or not host.strip():
    print("auth.json.host 缺失或无效", file=sys.stderr)
    raise SystemExit(1)
if not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?", host.strip()):
    print("auth.json.host 格式非法", file=sys.stderr)
    raise SystemExit(1)
if not isinstance(token, str) or not token.strip():
    print("auth.json.access_token 缺失或无效", file=sys.stderr)
    raise SystemExit(1)

print(host.strip())
print(token.strip())
PY
) || fail "读取 auth.json 失败"

HOST=$(printf '%s\n' "$AUTH_VALUES" | sed -n '1p')
ACCESS_TOKEN=$(printf '%s\n' "$AUTH_VALUES" | sed -n '2p')
[ -n "$HOST" ] || fail "auth.json.host 缺失或无效"
[ -n "$ACCESS_TOKEN" ] || fail "auth.json.access_token 缺失或无效"

TEMP_FILE=$(mktemp "${TMPDIR:-/tmp}/zqi-model-packages.XXXXXX") || fail "无法创建临时文件"
cleanup() {
    rm -f "$TEMP_FILE"
}
trap cleanup EXIT HUP INT TERM

HTTP_STATUS=$(curl -sS --max-time 30 \
    -o "$TEMP_FILE" \
    -w '%{http_code}' \
    "https://${HOST}/api/zqi/model-packages?${QUERY}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}") || fail "请求 model-packages 接口失败"

case "$HTTP_STATUS" in
    2[0-9][0-9])
        ;;
    *)
        fail "model-packages 接口返回 HTTP ${HTTP_STATUS}"
        ;;
esac

python3 - "$TEMP_FILE" <<'PY' || fail "接口响应不是合法 JSON"
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as response_file:
        json.load(response_file)
except (OSError, UnicodeDecodeError, json.JSONDecodeError):
    raise SystemExit(1)
PY

pbcopy < "$TEMP_FILE" || fail "写入剪切板失败"
printf '已复制完整 model-packages JSON 到剪切板。该内容包含敏感套餐信息，请勿粘贴到不安全的位置。\n'
