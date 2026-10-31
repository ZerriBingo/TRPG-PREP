"""An optional reasoning-effort hint reaches the upstream, and only when set."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import llm, llm_worker  # noqa: E402

BASE = {"base_url": "http://127.0.0.1:3050", "api_key": "unused", "model": "probe"}


def main() -> None:
    # The worker must not send the parameter when nothing is configured: an upstream
    # that does not recognise a value rejects the whole request with HTTP 400.
    off = llm_worker.build_payload({**BASE, "messages": [], "reasoning_effort": ""})
    assert "reasoning_effort" not in off, off
    missing = llm_worker.build_payload({**BASE, "messages": []})
    assert "reasoning_effort" not in missing, missing

    on = llm_worker.build_payload({**BASE, "messages": [], "reasoning_effort": "low"})
    assert on["reasoning_effort"] == "low", on
    assert on["max_tokens"] == 8000 and on["temperature"] == 0.3, on
    assert "stream" not in on, on
    assert llm_worker.build_payload(
        {**BASE, "messages": [], "reasoning_effort": "high", "stream": True}
    )["stream"] is True

    # The client must hand the configured value to the worker subprocess.
    def fake_run(command, input=None, **kwargs):  # noqa: A002
        captured.update(json.loads(input))

        class Result:
            returncode = 0
            stdout = json.dumps({"content": "ok", "finish_reason": "stop"})
            stderr = ""

        return Result()

    captured: dict = {}
    client = llm.LLMClient({**BASE, "reasoning_effort": "low"})
    with patch.object(llm.subprocess, "run", fake_run):
        assert client.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert captured.get("reasoning_effort") == "low", captured

    captured.clear()
    plain = llm.LLMClient(BASE)
    with patch.object(llm.subprocess, "run", fake_run):
        assert plain.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert captured.get("reasoning_effort") == "", captured

    # An upstream that cannot honour the hint makes a reasoning model spend the whole
    # reply thinking.  That must not fail the run: the client drops the hint and repeats
    # the call, and later calls stop sending it.
    calls: list[dict] = []

    def budget_exhausted(command, input=None, **kwargs):  # noqa: A002
        payload = json.loads(input)
        calls.append(payload)

        class Result:
            returncode = 0
            stderr = ""

            @property
            def stdout(self) -> str:
                if payload.get("reasoning_effort"):
                    return json.dumps({"content": None, "finish_reason": "length"})
                return json.dumps({"content": "recovered", "finish_reason": "stop"})

        return Result()

    recovering = llm.LLMClient({**BASE, "reasoning_effort": "high"})
    with patch.object(llm.subprocess, "run", budget_exhausted):
        assert recovering.chat([{"role": "user", "content": "hi"}]) == "recovered"
        assert recovering.reasoning_effort == "", "the unusable hint must be dropped"
        assert recovering.chat([{"role": "user", "content": "hi"}]) == "recovered"
    assert calls[0]["reasoning_effort"] == "high", calls[0]
    assert calls[1]["reasoning_effort"] == "", calls[1]
    assert calls[2]["reasoning_effort"] == "", calls[2]

    print("PASS: reasoning_effort is forwarded only when configured, and never blocks a run")


if __name__ == "__main__":
    main()
