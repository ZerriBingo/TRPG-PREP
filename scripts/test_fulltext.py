"""完整原文 + 锚点清单的 prompt 构造单测。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app import generate  # noqa: E402

stage_kb = {
    "locations": [{"name": "灯塔", "page": 5}, {"name": "地下室", "page": 8}],
    "events": [{"name": "火灾", "page": 6}],
    "clues": [{"clue": "神秘符文", "page": 7}],
}
campaign = {"id": 1, "name": "测试"}
snippet = "第 5 页的完整原文内容……" * 20

# locations
msgs = generate.build_stage_messages("locations", campaign, stage_kb, snippet, "第一章", "5-9", None)
user = msgs[1]["content"]
assert "场景清单" in user and "灯塔" in user and "地下室" in user, "锚点缺失"
assert "完整原文" in user and "第 5 页的完整原文" in user, "原文缺失"
assert "必须全部输出" in user, "覆盖要求缺失"
print("PASS: locations prompt 含锚点+完整原文+覆盖要求, 长度:", len(user))

# encounters
msgs = generate.build_stage_messages("encounters", campaign, stage_kb, snippet, "第一章", "5-9", None)
user2 = msgs[1]["content"]
assert "素材锚点" in user2 and "火灾" in user2 and "神秘符文" in user2, "素材锚点缺失"
assert "遭遇时刻" in user2, "遭遇要求缺失"
print("PASS: encounters prompt 含素材锚点+遭遇要求, 长度:", len(user2))

# 空锚点不报错
msgs3 = generate.build_stage_messages("locations", campaign, {"locations": []}, "", "第一章", "5-9", None)
assert "--- 本章场景清单" not in msgs3[1]["content"], "锚点应为空"
print("PASS: 空锚点容错")
print("全部通过")
