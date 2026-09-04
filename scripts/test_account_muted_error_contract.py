"""Regression contract for non-retryable upstream account access failures."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import llm, prep, shadow  # noqa: E402


client = llm.LLMClient({
    "base_url": "https://provider.invalid",
    "api_key": "redacted",
    "model": "fixture",
})
calls = 0


def account_muted_worker(*_args, **_kwargs):
    global calls
    calls += 1
    return {
        "error": "HTTP 403",
        "kind": "http",
        "status": 403,
        "body": '{"error":{"code":"account_muted","message":"account muted"}}',
    }


client._worker_call = account_muted_worker  # type: ignore[method-assign]
original_attempts = llm.RETRY_ATTEMPTS
llm.RETRY_ATTEMPTS = 3
try:
    try:
        client.chat([{"role": "user", "content": "fixture"}])
    except RuntimeError as error:
        message = str(error)
    else:
        raise AssertionError("account_muted must fail the request")
finally:
    llm.RETRY_ATTEMPTS = original_attempts

assert calls == 1
assert "account_muted" in message
assert "账号" in message
assert shadow._transport_error_kind(message) == "account_access"
assert prep._prep_error_kind(message) == "account_access"
frontend = (ROOT / "frontend" / "workbench.js").read_text(encoding="utf-8")
assert "account_access" in frontend
assert "账号、密钥、代理或供应商状态" in frontend
print("PASS: account_muted is actionable and never retried as a model failure")
