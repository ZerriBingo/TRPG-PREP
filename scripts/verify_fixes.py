"""修复验证：URL 规范化单测 + 前端 JS 语法检查（不需要服务在跑）。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
fails: list[str] = []

sys.path.insert(0, str(ROOT / "backend"))
from app.llm import chat_url, models_url  # noqa: E402

cases = [
    # (输入 base_url, 期望的 chat/completions URL)
    ("https://api.deepseek.com", "https://api.deepseek.com/v1/chat/completions"),
    ("https://api.deepseek.com/", "https://api.deepseek.com/v1/chat/completions"),
    ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1/chat/completions"),
    ("https://open.bigmodel.cn/api/paas/v4", "https://open.bigmodel.cn/api/paas/v4/chat/completions"),
    ("https://api.deepseek.com/v1/chat/completions", "https://api.deepseek.com/v1/chat/completions"),
]
for inp, want in cases:
    got = chat_url(inp)
    ok = got == want
    print(f"[{'PASS' if ok else 'FAIL'}] chat_url({inp!r}) -> {got}")
    if not ok:
        fails.append(f"chat_url:{inp}")

mcases = [
    ("https://api.deepseek.com", "https://api.deepseek.com/v1/models"),
    ("https://open.bigmodel.cn/api/paas/v4", "https://open.bigmodel.cn/api/paas/v4/models"),
    ("https://api.deepseek.com/v1/chat/completions", "https://api.deepseek.com/v1/models"),
]
for inp, want in mcases:
    got = models_url(inp)
    ok = got == want
    print(f"[{'PASS' if ok else 'FAIL'}] models_url({inp!r}) -> {got}")
    if not ok:
        fails.append(f"models_url:{inp}")

r = subprocess.run(["node", "--check", str(ROOT / "frontend" / "workbench.js")],
                   capture_output=True, text=True)
ok = r.returncode == 0
print(f"[{'PASS' if ok else 'FAIL'}] node --check workbench.js" + ("" if ok else f"\n{r.stderr}"))
if not ok:
    fails.append("node-check")

print("\n结果:", "全部通过" if not fails else f"失败项: {fails}")
sys.exit(1 if fails else 0)
