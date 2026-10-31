"""An upstream reply with null content must not crash as an AttributeError."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import llm  # noqa: E402


def main() -> None:
    client = llm.LLMClient(
        {"base_url": "http://127.0.0.1:3050", "api_key": "unused", "model": "probe"}
    )

    # A reasoning-only or tool-call-only reply arrives as content: null.  chat() must
    # still hand back text so chat_json can report an empty response.
    client._worker_call = lambda *args, **kwargs: {"content": None}  # type: ignore[method-assign]
    assert client.chat([{"role": "user", "content": "hi"}]) == ""
    try:
        client.chat_json([{"role": "user", "content": "hi"}])
    except RuntimeError as error:
        assert "空响应" in str(error), error
    except AttributeError as error:  # pragma: no cover - the regression this pins
        raise AssertionError(f"null content must not surface as AttributeError: {error}") from error
    else:  # pragma: no cover - defensive
        raise AssertionError("chat_json must raise on an empty response")

    # A missing content key behaves the same way rather than raising KeyError.
    client._worker_call = lambda *args, **kwargs: {}  # type: ignore[method-assign]
    assert client.chat([{"role": "user", "content": "hi"}]) == ""

    # Ordinary text still passes through unchanged.
    client._worker_call = lambda *args, **kwargs: {"content": "  hello  "}  # type: ignore[method-assign]
    assert client.chat([{"role": "user", "content": "hi"}]) == "  hello  "

    print("PASS: a null upstream content field reports an empty response, not an AttributeError")


if __name__ == "__main__":
    main()
