#!/usr/bin/env bash

# 验证启动脚本能够在缺少虚拟环境时创建环境、同步依赖并启动服务。
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
START_SCRIPT="$PROJECT_ROOT/start.sh"
TEST_ROOT="$(mktemp -d)"

cleanup() {
  rm -rf "$TEST_ROOT"
}

trap cleanup EXIT

assert_contains() {
  local expected="$1"
  local file_path="$2"

  if ! rg -Fqx "$expected" "$file_path"; then
    echo "断言失败：未找到 '$expected'" >&2
    exit 1
  fi
}

test_missing_venv_creates_and_syncs_before_starting() {
  local project_dir="$TEST_ROOT/project"
  local fake_bin_dir="$TEST_ROOT/bin"
  local log_file="$TEST_ROOT/commands.log"

  mkdir -p "$project_dir" "$fake_bin_dir"
  touch "$project_dir/pyproject.toml"
  cp "$START_SCRIPT" "$project_dir/start.sh"

  cat > "$fake_bin_dir/uv" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'uv %s\n' "$*" >> "$TEST_LOG_FILE"

case "$1" in
  venv)
    mkdir -p .venv/bin
    cat > .venv/bin/activate <<'ACTIVATE'
export PATH="$(pwd)/.venv/bin:$PATH"
ACTIVATE
    cat > .venv/bin/python <<'PYTHON'
#!/usr/bin/env bash
printf 'python %s\n' "$*" >> "$TEST_LOG_FILE"
PYTHON
    chmod +x .venv/bin/python
    ;;
  sync)
    ;;
  *)
    echo "意外的 uv 命令：$*" >&2
    exit 1
    ;;
esac
EOF
  chmod +x "$fake_bin_dir/uv"

  (
    cd "$project_dir"
    TEST_LOG_FILE="$log_file" PATH="$fake_bin_dir:$PATH" bash ./start.sh --help
  )

  assert_contains "uv venv $project_dir/.venv" "$log_file"
  assert_contains "uv sync --active" "$log_file"
  assert_contains "python -m src.main --help" "$log_file"
}

test_missing_venv_creates_and_syncs_before_starting
