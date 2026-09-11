"""JSON retries explain format failures without changing the request or retry count."""
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.llm import LLMClient  # noqa: E402

client = object.__new__(LLMClient)
messages = [{"role": "system", "content": 'Return {"title":"...","summary":"..."}.'}]
requests = []

def respond(prompt, **kwargs):
    requests.append(prompt)
    return '标题：索引\n摘要：普通文章' if len(requests) == 1 else '{"title":"Index","summary":"Summary"}'

with patch.object(client, "chat", side_effect=respond):
    assert client.chat_json(messages) == {"title": "Index", "summary": "Summary"}
assert len(requests) == 2
assert len(requests[1]) > len(requests[0])
assert "JSON object" in requests[1][-1]["content"]
assert len(messages) == 1
with patch.object(client, "chat", return_value="标题：索引") as chat:
    try:
        client.chat_json(messages)
    except RuntimeError as error:
        assert "未返回 JSON 对象" in str(error)
        assert "截断" not in str(error)
    else:
        raise AssertionError("plain prose must fail")
    assert chat.call_count == 2
print("PASS: format feedback, accurate error, unchanged input and bounded retries")
