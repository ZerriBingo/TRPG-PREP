"""LLM worker 子进程：主线程用 curl_cffi 发请求，结果经 stdout JSON 返回。

独立进程避开 uvicorn 工作线程与 libcurl 的冲突（挂死/超时不生效问题）。
stdin 输入 JSON: {"base_url","api_key","model","messages","temperature","max_tokens","stream"}
stdout 输出 JSON: {"content": ...} 或
{"error": "...", "kind": "http|network|timeout|response|worker", "status": 429}
"""
import json
import sys
import socket

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


def build_payload(cfg: dict) -> dict:
    """Build the upstream request body from the worker's input.

    A reasoning-effort hint is forwarded only when one is configured, because an
    upstream that does not recognise the value rejects the whole request.
    """
    payload = {
        "model": cfg["model"],
        "messages": cfg["messages"],
        "temperature": cfg.get("temperature", 0.3),
        "max_tokens": cfg.get("max_tokens", 8000),
    }
    if cfg.get("stream"):
        payload["stream"] = True
    effort = str(cfg.get("reasoning_effort") or "").strip()
    if effort:
        payload["reasoning_effort"] = effort
    return payload


def main() -> None:
    cfg = json.loads(sys.stdin.read())
    url = _url(cfg["base_url"])
    headers = {"Authorization": f"Bearer {cfg['api_key']}", "User-Agent": BROWSER_UA}
    payload = build_payload(cfg)
    try:
        request_timeout = cfg.get("request_timeout")
        read_timeout = max(float(request_timeout or 240), 1.0)
        r = creq.post(url, impersonate="chrome", timeout=(10, read_timeout),
                      headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        if cfg.get("stream"):
            # 非流式响应直接取 content
            content = choice["message"]["content"]
        else:
            content = choice["message"]["content"]
        # finish_reason tells a caller why a reply can be empty: a reasoning model that
        # spends the whole budget thinking returns content=null with finish_reason=length.
        # ensure_ascii keeps the frame pure ASCII: this process is started with the console
        # code page (GBK on Windows), so printing Chinese directly raised UnicodeEncodeError
        # for some characters and the call failed for a reason that had nothing to do with
        # the upstream.  JSON escapes survive any encoding and decode back to the same text.
        print(json.dumps({"content": content,
                          "finish_reason": choice.get("finish_reason")}, ensure_ascii=True))
    except Exception as e:  # noqa: BLE001
        response = getattr(e, "response", None)
        status = getattr(response, "status_code", None)
        body = ""
        try:
            body = response.text[:500] if response is not None else ""
        except Exception:  # noqa: BLE001
            pass
        error_name = type(e).__name__
        error_text = str(e)
        lowered = error_text.casefold()
        retry_after = ""
        try:
            if response is not None:
                retry_after = str((response.headers or {}).get("retry-after") or "")
        except Exception:  # noqa: BLE001
            retry_after = ""
        if response is not None and isinstance(status, int) and status > 0:
            kind = "http"
        elif "timed out" in lowered or "timeout" in lowered or "operation timed out" in lowered:
            kind = "timeout"
        elif isinstance(e, (ConnectionError, TimeoutError, OSError, socket.error)) or "curl:" in lowered:
            kind = "network"
        else:
            kind = "response"
        print(json.dumps({"error": f"{type(e).__name__}: {e}",
                          "kind": kind, "status": status, "body": body,
                          "error_type": error_name, "retry_after": retry_after,
                          "timeout": cfg.get("request_timeout")}, ensure_ascii=True))


if __name__ == "__main__":
    main()
