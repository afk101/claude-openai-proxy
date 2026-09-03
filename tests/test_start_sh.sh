#!/usr/bin/env bash

# 验证启动脚本能够在缺少虚拟环境时创建环境、同步依赖并启动服务。
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
START_SCRIPT="$PROJECT_ROOT/start.sh"
TEST_ROOT="$(mktemp -d)"

# 无论测试成功或失败都只删除本用例创建的临时根目录，避免残留伪环境。
cleanup() {
  rm -rf "$TEST_ROOT"
}

trap cleanup EXIT

# 按完整行校验伪命令日志，避免子串匹配掩盖参数边界或顺序错误。
assert_contains() {
  local expected="$1"
  local file_path="$2"

  if ! rg -Fqx "$expected" "$file_path"; then
    echo "断言失败：未找到 '$expected'" >&2
    exit 1
  fi
}

# 模拟首次启动，验证脚本先创建虚拟环境、同步依赖，再原样传递帮助参数。
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

# 模拟只有目录而没有解释器的不完整环境，验证脚本使用 --clear 进行可恢复重建。
test_incomplete_venv_is_rebuilt_and_synced() {
  local project_dir="$TEST_ROOT/incomplete-project"
  local fake_bin_dir="$TEST_ROOT/incomplete-bin"
  local log_file="$TEST_ROOT/incomplete-commands.log"

  mkdir -p "$project_dir/.venv" "$fake_bin_dir"
  touch "$project_dir/pyproject.toml"
  cp "$START_SCRIPT" "$project_dir/start.sh"

  cat > "$fake_bin_dir/uv" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'uv %s\n' "$*" >> "$TEST_LOG_FILE"
if [[ "$1" == "venv" ]]; then
  mkdir -p .venv/bin
  cat > .venv/bin/activate <<'ACTIVATE'
export PATH="$(pwd)/.venv/bin:$PATH"
ACTIVATE
  cat > .venv/bin/python <<'PYTHON'
#!/usr/bin/env bash
printf 'python %s\n' "$*" >> "$TEST_LOG_FILE"
PYTHON
  chmod +x .venv/bin/python
fi
EOF
  chmod +x "$fake_bin_dir/uv"

  TEST_LOG_FILE="$log_file" PATH="$fake_bin_dir:$PATH" \
    bash "$project_dir/start.sh" --help

  assert_contains "uv venv --clear $project_dir/.venv" "$log_file"
  assert_contains "uv sync --active" "$log_file"
  assert_contains "python -m src.main --help" "$log_file"
}

# 从仓库外启动完整环境，验证不会重复同步，并保持包含空格的参数边界。
test_complete_venv_runs_from_external_cwd_without_sync_and_preserves_arguments() {
  local project_dir="$TEST_ROOT/complete-project"
  local outside_dir="$TEST_ROOT/outside"
  local fake_bin_dir="$TEST_ROOT/complete-bin"
  local log_file="$TEST_ROOT/complete-commands.log"

  mkdir -p "$project_dir/.venv/bin" "$outside_dir" "$fake_bin_dir"
  touch "$project_dir/pyproject.toml"
  cp "$START_SCRIPT" "$project_dir/start.sh"
  cat > "$project_dir/.venv/bin/activate" <<'ACTIVATE'
export PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd):$PATH"
ACTIVATE
  cat > "$project_dir/.venv/bin/python" <<'PYTHON'
#!/usr/bin/env bash
printf 'python' >> "$TEST_LOG_FILE"
printf ' <%s>' "$@" >> "$TEST_LOG_FILE"
printf '\n' >> "$TEST_LOG_FILE"
PYTHON
  chmod +x "$project_dir/.venv/bin/python"
  cat > "$fake_bin_dir/uv" <<'UV'
#!/usr/bin/env bash
echo "完整环境不应调用 uv" >&2
exit 99
UV
  chmod +x "$fake_bin_dir/uv"

  (
    cd "$outside_dir"
    TEST_LOG_FILE="$log_file" PATH="$fake_bin_dir:$PATH" \
      bash "$project_dir/start.sh" --host 127.0.0.1 --label "value with spaces"
  )

  assert_contains \
    "python <-m> <src.main> <--host> <127.0.0.1> <--label> <value with spaces>" \
    "$log_file"
  if rg -q '^uv ' "$log_file"; then
    echo "断言失败：完整虚拟环境不应重复调用 uv" >&2
    exit 1
  fi
}

# 静态检查启动器不会把 .env 当作 Shell 程序执行，配置只交给 python-dotenv。
test_start_script_does_not_execute_dotenv_as_shell_code() {
  if rg -n 'source[^#]*\.env' "$START_SCRIPT" >/dev/null; then
    echo "断言失败：start.sh 不得 source .env" >&2
    exit 1
  fi
}

test_missing_venv_creates_and_syncs_before_starting
test_incomplete_venv_is_rebuilt_and_synced
test_complete_venv_runs_from_external_cwd_without_sync_and_preserves_arguments
test_start_script_does_not_execute_dotenv_as_shell_code
