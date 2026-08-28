"""冒烟验证 v2：不碰用户真实配置与 key。
- HTTP：建战役/上传/摄入/retry 端点错误路径（不调真实 LLM）
- 单测：FakeLLM 全流程成功；BoomClient 验证失败跳过与降级
"""
import json
import re
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parent.parent
PDF = next(Path(ROOT.parent).glob("*.pdf"), None)
assert PDF is not None, "未找到模组 PDF"
fails = []


def check(name, cond, extra=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {extra}")
    if not cond:
        fails.append(name)


c = httpx.Client(base_url=BASE, timeout=60)

# ---------- HTTP 部分（不花钱） ----------
r = c.post("/api/campaigns", json={"name": "分块冒烟"})
cid = r.json()["id"]
with open(PDF, "rb") as f:
    c.post(f"/api/campaigns/{cid}/upload", files={"file": (PDF.name, f, "application/pdf")})
rep = c.post(f"/api/campaigns/{cid}/ingest").json()
titles = [ch["title"] for ch in rep["chunks"]]
check("摄入分块", len(titles) >= 2, f"count={len(titles)}")

# retry 端点：不存在的标题 -> error 事件（不调 LLM）
r = c.post(f"/api/campaigns/{cid}/analyze/retry", json={"chunks": ["不存在的分块"]})
evs = [json.loads(x[6:]) for x in r.text.splitlines() if x.startswith("data: ")]
err = next((e for e in evs if e["type"] == "error"), None)
check("retry 错误路径", err is not None, f"msg={err and err['message'][:40]}")

# 清理冒烟战役
c.delete(f"/api/campaigns/{cid}")
check("冒烟战役已清理", c.get(f"/api/campaigns/{cid}").status_code == 404)

# ---------- 单测：直调 analyze（不走 HTTP/make_client） ----------
sys.path.insert(0, str(ROOT / "backend"))
from app import analyze  # noqa: E402
from app.llm import FakeLLM  # noqa: E402

fake = FakeLLM({"base_url": "http://x", "model": "fake", "fake": True, "api_key": ""})

# 建一个真实战役用于直调（有分块即可，FakeLLM 不调网络）
r = c.post("/api/campaigns", json={"name": "直调分析"})
cid2 = r.json()["id"]
with open(PDF, "rb") as f:
    c.post(f"/api/campaigns/{cid2}/upload", files={"file": (PDF.name, f, "application/pdf")})
c.post(f"/api/campaigns/{cid2}/ingest")

evs = list(analyze.run_analysis(cid2, fake))
done = next((e for e in evs if e["type"] == "done"), None)
check("FakeLLM 集合分析 done", done is not None and not done["failed"],
      f"failed={done and done['failed']}")
check("FakeLLM 知识库有内容", bool(done) and sum(len(v) for v in done["data"].values()) > 0,
      f"summary={done and done['knowledge']}")

# BoomClient：让"前言/目录"块失败，验证跳过 + 其余保留
class BoomClient:
    def chat_json(self, messages, **kw):
        content = messages[-1]["content"]
        m = re.search(r"分块：(.+?)（", content)
        title = m.group(1) if m else "?"
        if "前言" in title:
            raise RuntimeError("模拟失败")
        return {"locations": [{"name": f"实体-{title[:6]}", "page": 1}],
                "npcs": [], "items": [], "events": [], "clues": [], "timeline": []}

evs = list(analyze.run_analysis(cid2, BoomClient()))
done3 = next((e for e in evs if e["type"] == "done"), None)
warns = [e for e in evs if e["type"] == "warn"]
check("失败块被跳过并记录",
      done3 is not None and any(f["title"] == "前言/目录" for f in done3["failed"]),
      f"warns={len(warns)} failed={done3 and [f['title'] for f in done3['failed']]}")
check("其他块结果保留", done3 is not None and len(done3["data"]["locations"]) >= 1,
      f"locations={done3 and len(done3['data']['locations'])}")

# 清理直调战役
c.delete(f"/api/campaigns/{cid2}")

print(f"\n结果: {'全部通过' if not fails else '失败项: ' + str(fails)}")
sys.exit(1 if fails else 0)
