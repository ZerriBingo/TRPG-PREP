"""LLM worker 子进程：主线程用 curl_cffi 发请求，结果经 stdout JSON 返回。

独立进程避开 uvicorn 工作线程与 libcurl 的冲突（挂死/超时不生效问题）。
stdin 输入 JSON: {"base_url","api_key","model","messages","temperature","max_tokens","stream"}
stdout 输出 JSON: {"content": ...} 或 {"error": "..."}
"""
import json
import sys

import curl_cffi.requests as creq

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.rstrip("/").endswith(("/v1", "/v2", "/v3", "/v4")):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def main() -> None:
    cfg = json.loads(sys.stdin.read())
    url = _url(cfg["base_url"])
    headers = {"Authorization": f"Bearer {cfg['api_key']}", "User-Agent": BROWSER_UA}
    payload = {
        "model": cfg["model"],
        "messages": cfg["messages"],
        "temperature": cfg.get("temperature", 0.3),
        "max_tokens": cfg.get("max_tokens", 8000),
    }
    if cfg.get("stream"):
        payload["stream"] = True
    try:
        request_timeout = cfg.get("request_timeout")
        read_timeout = max(float(request_timeout or 240), 1.0)
        r = creq.post(url, impersonate="chrome", timeout=(10, read_timeout),
                      headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
        if cfg.get("stream"):
            # 非流式响应直接取 content
            content = data["choices"][0]["message"]["content"]
        else:
            content = data["choices"][0]["message"]["content"]
        print(json.dumps({"content": content}, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        status = getattr(getattr(e, "response", None), "status_code", None)
        body = ""
        try:
            body = e.response.text[:200] if getattr(e, "response", None) is not None else ""
        except Exception:  # noqa: BLE001
            pass
        print(json.dumps({"error": f"{type(e).__name__}: {e}",
                          "status": status, "body": body}, ensure_ascii=False))


if __name__ == "__main__":
    main()
