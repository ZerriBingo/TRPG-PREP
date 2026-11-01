"""A rate-limited call waits what the upstream asked and retries."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import llm  # noqa: E402

BASE = {"base_url": "http://127.0.0.1:3050", "api_key": "unused", "model": "probe"}
RATE_LIMIT_BODY = json.dumps(
    {
        "error": {
            "message": "Empty response from upstream (zero output tokens)",
            "type": "rate_limit_error",
        },
        "retry_after": 10,
    }
)


def main() -> None:
    # The wait an upstream states is honoured, and the call is retried rather than failing the
    # window.  A single attempt made this branch unreachable before.
    assert llm._retry_after({"body": RATE_LIMIT_BODY}) == 10.0
    assert llm._retry_after({"retry_after": "7"}) == 7.0
    assert llm._retry_after({"body": "no hint here"}) is None
    assert llm._retry_delay(0, 429, 10.0) == 10.0
    assert llm._retry_delay(0, 429, 600.0) == llm.MAX_RETRY_WAIT
    assert llm._retry_delay(0, 429) == 2.0
    assert llm.RETRY_ATTEMPTS > 1, "the retry branch must be reachable"

    calls: list[dict] = []

    def fake_run(command, input=None, **kwargs):  # noqa: A002
        calls.append(json.loads(input))

        class Result:
            returncode = 0
            stderr = ""

            @property
            def stdout(self) -> str:
                if len(calls) == 1:
                    return json.dumps(
                        {
                            "error": "HTTPError: rate limited",
                            "kind": "http",
                            "status": 429,
                            "body": RATE_LIMIT_BODY,
                            "error_type": "HTTPError",
                        }
                    )
                return json.dumps({"content": "recovered", "finish_reason": "stop"})

        return Result()

    slept: list[float] = []
    client = llm.LLMClient(BASE)
    with patch.object(llm.subprocess, "run", fake_run), patch("time.sleep", slept.append):
        assert client.chat([{"role": "user", "content": "hi"}]) == "recovered"
    assert len(calls) == 2, calls
    assert slept == [10.0], slept

    # A status that is not retryable still fails immediately.
    def always_500(command, input=None, **kwargs):  # noqa: A002
        class Result:
            returncode = 0
            stdout = json.dumps(
                {"error": "HTTPError: boom", "kind": "http", "status": 500,
                 "body": "server error", "error_type": "HTTPError"}
            )
            stderr = ""

        return Result()

    failed: list[float] = []
    with patch.object(llm.subprocess, "run", always_500), patch("time.sleep", failed.append):
        try:
            llm.LLMClient(BASE).chat([{"role": "user", "content": "hi"}])
        except RuntimeError as error:
            assert "500" in str(error), error
        else:
            raise AssertionError("a non-retryable status must fail")
    assert failed == [], failed

    print("PASS: a rate limit is retried for the wait the upstream asked")


if __name__ == "__main__":
    main()
