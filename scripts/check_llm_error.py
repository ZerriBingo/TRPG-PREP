"""验证真实 LLMClient 的错误路径：无效端点应抛清晰异常而非挂死。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app.llm import LLMClient  # noqa: E402

c = LLMClient({"base_url": "http://127.0.0.1:9", "api_key": "bad-key", "model": "x"})
try:
    c.chat([{"role": "user", "content": "hi"}])
    print("FAIL: 应该抛异常却没有")
    sys.exit(1)
except Exception as e:
    print(f"OK 干净报错: {type(e).__name__}: {str(e)[:120]}")
