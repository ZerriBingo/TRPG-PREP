"""验证 _filter_kb：各部分的字段过滤 + 大小（应 < 30000 不截断）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.analyze import knowledge_summary_text  # noqa: E402
from app.generate import PART_FIELDS, _filter_kb  # noqa: E402


def fixture_knowledge_base() -> dict:
    return {
        "story": "一组调查员追查灯塔附近反复出现的失踪事件。",
        "timeline": [{"time": "午夜", "event": "灯塔熄灭"}],
        "ending": [{"name": "封锁灯塔", "desc": "调查员封存地下入口。"}],
        "locations": [{"name": "旧灯塔", "page": 12, "desc": "海边的废弃灯塔。"}],
        "npcs": [{"name": "守塔人", "page": 13, "knows": ["地下入口"]}],
        "clues": [{"clue": "湿脚印", "page": 14}],
        "items": [{"name": "黄铜钥匙", "page": 15}],
        "events": [{"name": "停电", "page": 16, "desc": "整座灯塔突然失去照明。"}],
        "unexpected_field": ["must not leak into a part prompt"],
    }


kb = fixture_knowledge_base()

for part in ("overview", "locations", "encounters"):
    fk = _filter_kb(kb, part)
    s = knowledge_summary_text(fk)
    print(f"{part}: keys={list(fk.keys())} chars={len(s)} 截断={'是!!' if len(s) >= 30000 else '否'}")
    assert set(fk) == set(PART_FIELDS[part]) & set(kb)
    assert "unexpected_field" not in s
    assert len(s) < 30000
    if part == "overview":
        assert kb["story"][:20] in s
        print("  story 在 prompt 里: True")
