"""merge_knowledge story/ending 单测。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.analyze import merge_knowledge  # noqa: E402

partials = [
    {
        "locations": [{"name": "酒店", "page": 1}],
        "ending": [{"name": "官方结局A", "page": 20, "desc": "描述A"}],
        "story": "第一章：侦探抵达酒店调查失踪案。",
        "npcs": [], "items": [], "events": [], "clues": [], "timeline": [],
    },
    {
        "locations": [{"name": "地下室", "page": 3}],
        "ending": [{"name": "官方结局A", "page": 20, "desc": "描述A2"}, {"name": "坏结局", "page": 21}],
        "story": "第二章：发现邪教祭祀现场。",
        "npcs": [], "items": [], "events": [], "clues": [], "timeline": [],
    },
]
kb = merge_knowledge(partials)
print("story:", kb["story"][:60])
print("endings:", kb["ending"])
assert "第一章" in kb["story"] and "第二章" in kb["story"], "story 应拼接"
assert len(kb["ending"]) == 2, f"ending 应去重合并为 2，实际 {len(kb['ending'])}"
names = [e["name"] for e in kb["ending"]]
assert "官方结局A" in names and "坏结局" in names, names
print("PASS: merge_knowledge story 拼接 + ending 去重合并正常")
