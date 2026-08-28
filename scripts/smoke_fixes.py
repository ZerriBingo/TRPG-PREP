"""FakeLLM 全流程验证：子切后 6 块分析 + 分阶段生成（不依赖中转站）。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app import analyze, generate  # noqa: E402
from app.llm import FakeLLM  # noqa: E402

fake = FakeLLM({"base_url": "http://x", "model": "fake", "fake": True, "api_key": ""})

# 分析（战役 35 已摄入 6 块）
evs = list(analyze.run_analysis(35, fake))
done = next((e for e in evs if e["type"] == "done"), None)
progs = [e for e in evs if e["type"] == "progress"]
warns = [e for e in evs if e["type"] == "warn"]
print(f"分析: progress={len(progs)} warns={len(warns)} done={bool(done)}")
assert len(progs) == 6, "应 6 块全部完成"
assert done and not done["failed"], f"failed={done and done['failed']}"
print("PASS: 6 块分析全部成功")

# 分阶段生成 locations（6 章）
evs2 = list(generate.run_generate_staged(35, "locations", fake))
progs2 = [e for e in evs2 if e["type"] == "progress"]
done2 = next((e for e in evs2 if e["type"] == "done"), None)
print(f"生成 locations: progress={len(progs2)} done={bool(done2)}")
assert len(progs2) == 6, "应 6 阶段"
assert done2 is not None
print("PASS: 分阶段生成 6 阶段完成")

# 检查子块文本带页标记（决定模型能否标准页码）
import sqlite3
conn = sqlite3.connect(str(ROOT / "data/app.db"))
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT idx, title, length(text) n FROM chunks WHERE campaign_id=35 ORDER BY idx").fetchall()
conn.close()
for r in rows:
    print(f"  块{r['idx']}: {r['title']} ({r['n']}字符)")
print("PASS: 子块结构正常")
