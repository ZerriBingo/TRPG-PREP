import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.llm import parse_json  # noqa: E402

cases = [
    '{"a": 1, "b": [1,2',
    '{"timeline": [{"time":"1989","event":"A"},{"time":"1990","event":"B"},{"event":"C truncated',
    '{"npcs": [{"name":"HuaJin"},{"name":"Selasir"',
    '{"a": {"x": 1}}',
]
for c in cases:
    try:
        print("PASS:", parse_json(c))
    except Exception as e:
        print("FAIL:", type(e).__name__, str(e)[:60])
