"""分阶段生成冒烟：解析/过滤/合并单测 + FakeLLM 直调多阶段流程。"""
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app import generate  # noqa: E402
from app import analyze  # noqa: E402
from app.llm import FakeLLM  # noqa: E402

# 1) _parse_pages
assert generate._parse_pages("7-12") == (7, 12)
assert generate._parse_pages("3") == (0, 10**9)
print("PASS: _parse_pages")

# 2) _filter_by_page
kb = {"locations": [{"name": "A", "page": 3}, {"name": "B", "page": 9}], "npcs": [{"name": "N", "page": 10}]}
out = generate._filter_by_page(kb, "locations", 7, 12)
assert [x["name"] for x in out["locations"]] == ["B"], out
assert out["npcs"][0]["name"] == "N"
print("PASS: _filter_by_page")

# 3) _merge_staged：同名场景合并
r1 = {"locations": [{"name": "灯塔", "page": 5, "read_aloud": "塔影", "info": [{"title": "塔顶", "disclosure": ["x"]}], "npcs": ["看守"]}]}
r2 = {"locations": [{"name": "灯塔", "page": 5, "description": "补全", "info": [{"title": "塔顶", "disclosure": ["y"]}, {"title": "地窖"}], "npcs": ["看守", "旅人"]}]}
m = generate._merge_staged("locations", [r1, r2])
assert len(m["locations"]) == 1, m
loc = m["locations"][0]
assert loc["description"] == "补全"
assert len(loc["info"]) == 2, loc["info"]  # 塔顶去重 + 地窖新增
assert loc["npcs"] == ["看守", "旅人"]
print("PASS: _merge_staged locations 合并")

# 4) FakeLLM 直调 staged：progress 数 = 章节数，done 有数据
c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=60)
r = c.post("/api/campaigns", json={"name": "分阶段测试"})
cid = r.json()["id"]
PDF = next(Path(ROOT.parent).glob("*.pdf"), None)
with open(PDF, "rb") as f:
    c.post(f"/api/campaigns/{cid}/upload", files={"file": (PDF.name, f, "application/pdf")})
c.post(f"/api/campaigns/{cid}/ingest")

fake = FakeLLM({"base_url": "http://x", "model": "fake", "fake": True, "api_key": ""})
list(analyze.run_analysis(cid, fake))  # 先建知识库
evs = list(generate.run_generate_staged(cid, "locations", fake))
progs = [e for e in evs if e["type"] == "progress"]
done = next((e for e in evs if e["type"] == "done"), None)
warns = [e for e in evs if e["type"] == "warn"]
print(f"progress stages: {len(progs)}, done: {bool(done)}, warns: {len(warns)}")
assert len(progs) >= 2, f"应有多阶段，实际 {len(progs)}"
assert done is not None, "应有 done"
print(f"merged locations: {len(done['data'].get('locations', []))}")

# 5) overview 仍走单次（转发 run_generate）
evs2 = list(generate.run_generate_staged(cid, "overview", fake))
done2 = next((e for e in evs2 if e["type"] == "done"), None)
assert done2 is not None, "overview 应单次 done"
print("PASS: overview 转发单次")

c.delete(f"/api/campaigns/{cid}")
print("全部通过")
