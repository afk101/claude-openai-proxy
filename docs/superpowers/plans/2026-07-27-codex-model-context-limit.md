# Codex Model Output Default Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-subagent-driven-development OR superpowers-executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `claude-openai-proxy` 未指定输出额度时的默认 `max_tokens` 从 128000 调整为 64000。

**Architecture:** 保持现有 Responses → Chat Completions → Claude Messages 数据流不变，仅调整转换代理的集中默认常量。调用方显式额度继续优先，避免扩大到 token 估算或模型能力映射。

**Tech Stack:** Python 3.13、Pydantic、pytest

---

### Task 1: 调整默认输出额度

**Files:**
- Modify: `/Users/qihoo/Documents/A_Own/claude-openai-proxy/tests/test_conversion.py`
- Modify: `/Users/qihoo/Documents/A_Own/claude-openai-proxy/tests/test_api.py`
- Modify: `/Users/qihoo/Documents/A_Own/claude-openai-proxy/src/core/constants.py`

- [ ] **Step 1: 修改通用默认值测试期望**

将 `test_resolve_max_tokens_uses_generic_default_for_arbitrary_model` 的期望值从
`128000` 改为 `64000`，并同步更新 API 层对默认额度的请求转换断言。

- [ ] **Step 2: 运行目标测试并确认失败**

Run:

```bash
.venv/bin/python -m pytest tests/test_conversion.py::test_resolve_max_tokens_uses_generic_default_for_arbitrary_model -q
```

Expected: FAIL，实际值仍为 `128000`。

- [ ] **Step 3: 修改集中默认常量**

将 `Constants.DEFAULT_MAX_TOKENS` 从 `128000` 改为 `64000`。

- [ ] **Step 4: 运行转换相关测试**

Run:

```bash
.venv/bin/python -m pytest tests/test_conversion.py -q
```

Expected: PASS。

- [ ] **Step 5: 运行完整测试和编译验证**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall src tests
```

Expected: 全部通过。

- [ ] **Step 6: 提交实现**

```bash
git add src/core/constants.py tests/test_conversion.py
git commit -m "fix(conversion): lower default output token limit"
```
