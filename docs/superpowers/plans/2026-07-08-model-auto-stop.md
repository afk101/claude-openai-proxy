# Model Auto Stop Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-subagent-driven-development OR superpowers-executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement dynamic `max_tokens` mapping based on the requested model to prevent output truncation on modern Claude models (like Opus 4.8 and Fable 5).

**Architecture:** Add a `MODEL_MAX_TOKENS_MAP` to constants, and update `request_converter.py`'s `resolve_max_tokens` to use this map when client doesn't provide a value.

**Tech Stack:** Python, FastAPI (Proxy logic)

---

### Task 1: Add Model Max Tokens Map to Constants

**Files:**
- Modify: `src/core/constants.py`

- [x] **Step 1: Add the dictionary mapping**

In `src/core/constants.py`, below `DEFAULT_MAX_TOKENS = 4096`, add:

```python
    MODEL_MAX_TOKENS_MAP = {
        "claude-fable-5": 128000,
        "claude-mythos-5": 128000,
        "claude-opus-4-8": 128000,
        "claude-opus-4-7": 128000,
        "claude-sonnet-5": 128000,
        "claude-sonnet-4-6": 64000,
        "claude-haiku-4-5": 64000,
        "claude-haiku-4-5-20251001": 64000,
        "claude-3-5-sonnet-20241022": 8192,
        "claude-3-5-sonnet-20240620": 8192,
        "claude-3-5-haiku-20241022": 8192,
        "claude-3-opus-20240229": 4096,
        "claude-3-sonnet-20240229": 4096,
        "claude-3-haiku-20240307": 4096,
    }
```

- [ ] **Step 2: Commit**

```bash
git add src/core/constants.py
git commit -m "feat: add MODEL_MAX_TOKENS_MAP for modern Claude models"
```

### Task 2: Update Request Converter to Use Mapping

**Files:**
- Modify: `src/conversion/request_converter.py`

- [x] **Step 1: Update resolve_max_tokens function**

In `src/conversion/request_converter.py`, modify `resolve_max_tokens` to check the map:

```python
def resolve_max_tokens(request: OpenAIChatCompletionRequest) -> int:
    """解析 Claude 所需的 max_tokens，使用基于模型的动态映射。"""
    if request.max_tokens is not None:
        return request.max_tokens
    if request.max_completion_tokens is not None:
        return request.max_completion_tokens
        
    model = request.model
    if model in Constants.MODEL_MAX_TOKENS_MAP:
        return Constants.MODEL_MAX_TOKENS_MAP[model]
        
    # 尝试匹配前缀
    for key, limit in Constants.MODEL_MAX_TOKENS_MAP.items():
        if model.startswith(key):
            return limit
            
    return Constants.DEFAULT_MAX_TOKENS
```

- [x] **Step 2: Format the code**

Run `ruff format src/conversion/request_converter.py` or equivalent formatter.

- [x] **Step 3: Test the proxy locally (Verification)**

```bash
python -c "
from src.models.openai import OpenAIChatCompletionRequest
from src.conversion.request_converter import resolve_max_tokens

req = OpenAIChatCompletionRequest(model='claude-opus-4-8', messages=[])
print(f'Opus 4.8 Limit: {resolve_max_tokens(req)}')
assert resolve_max_tokens(req) == 128000

req_old = OpenAIChatCompletionRequest(model='claude-3-opus-20240229', messages=[])
print(f'Old Opus Limit: {resolve_max_tokens(req_old)}')
assert resolve_max_tokens(req_old) == 4096

req_override = OpenAIChatCompletionRequest(model='claude-opus-4-8', max_tokens=100, messages=[])
print(f'Override Limit: {resolve_max_tokens(req_override)}')
assert resolve_max_tokens(req_override) == 100

print('All tests passed!')
"
```

- [ ] **Step 4: Commit**

```bash
git add src/conversion/request_converter.py
git commit -m "fix: dynamically assign max_tokens based on model to prevent auto-stop"
```
