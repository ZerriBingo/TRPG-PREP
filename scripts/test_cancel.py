"""取消机制冒烟：cancel 后 run_analysis 应发 cancelled 事件并停止；cancel 端点应 200。"""
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app import analyze  # noqa: E402
from app.llm import FakeLLM  # noqa: E402

c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=60)

# 建真实战役并摄入（有分块才能进入循环检查）
r = c.post("/api/campaigns", json={"name": "取消测试"})
cid = r.json()["id"]
PDF = next(Path(ROOT.parent).glob("*.pdf"), None)
with open(PDF, "rb") as f:
    c.post(f"/api/campaigns/{cid}/upload", files={"file": (PDF.name, f, "application/pdf")})
c.post(f"/api/campaigns/{cid}/ingest")

# 直调：预先置 cancel 标志
fake = FakeLLM({"base_url": "http://x", "model": "fake", "fake": True, "api_key": ""})
analyze.cancel_analysis(cid)
evs = list(analyze.run_analysis(cid, fake))
types = [e["type"] for e in evs]
print("events:", types)
assert types == ["cancelled"], f"应只有 cancelled，实际 {types}"
print("PASS: 直调取消生效（第一个事件即 cancelled）")

# HTTP 端点
r = c.post(f"/api/campaigns/{cid}/analyze/cancel")
print("cancel endpoint:", r.status_code, r.json())
assert r.status_code == 200
print("PASS: cancel 端点 200")

# 清理
c.delete(f"/api/campaigns/{cid}")
print("PASS: 清理完成")
