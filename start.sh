#!/usr/bin/env bash

# 使用项目虚拟环境启动代理服务；环境缺失或不完整时自动通过 uv 修复。
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly VENV_DIR="$SCRIPT_DIR/.venv"
readonly ACTIVATE_SCRIPT="$VENV_DIR/bin/activate"

print_error() {
  echo "错误：$1" >&2
}

require_uv() {
  if ! command -v uv >/dev/null 2>&1; then
    print_error "未找到 uv。请先安装 uv：https://docs.astral.sh/uv/getting-started/installation/"
    exit 127
  fi
}

create_virtual_environment() {
  require_uv

  if [[ -d "$VENV_DIR" ]]; then
    echo "检测到不完整的 .venv，正在重新创建虚拟环境..."
    uv venv --clear "$VENV_DIR"
    return
  fi

  echo "未检测到 .venv，正在创建虚拟环境..."
  uv venv "$VENV_DIR"
}

synchronize_dependencies() {
  echo "正在同步项目依赖..."
  uv sync --active
}

main() {
  cd "$SCRIPT_DIR"

  if [[ ! -f "pyproject.toml" ]]; then
    print_error "未在脚本目录中找到 pyproject.toml：$SCRIPT_DIR"
    exit 1
  fi

  local needs_sync=false
  if [[ ! -f "$ACTIVATE_SCRIPT" || ! -x "$VENV_DIR/bin/python" ]]; then
    create_virtual_environment
    needs_sync=true
  fi

  source "$ACTIVATE_SCRIPT"

  if [[ "$needs_sync" == true ]]; then
    synchronize_dependencies
  fi

  if ! command -v python >/dev/null 2>&1; then
    print_error "虚拟环境中未找到 python：$VENV_DIR"
    exit 1
  fi

  exec python -m src.main "$@"
}

main "$@"
