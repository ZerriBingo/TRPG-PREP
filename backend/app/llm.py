"""LLM 网关：单一 OpenAI-compatible Chat Completions 适配层。

假客户端（FakeLLM）按提示中的 [TASK:...] 标记返回确定性的示例 JSON，
用于在无 API key 时跑通全流程验证。
"""
from __future__ import annotations

import json
import re
import time
from typing import Iterator

import subprocess
import sys
from pathlib import Path

import curl_cffi.requests as creq

from . import storage

# 部分中转站（new-api 类）按 User-Agent 拦截脚本/自动化客户端（ua_blocked_script_client），
# 因此请求需携带浏览器 UA。
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def auth_headers(cfg: dict) -> dict:
    return {"Authorization": f"Bearer {cfg['api_key']}", "User-Agent": BROWSER_UA}

class _Retry(Exception):
    """内部信号：本次请求可重试（限流/上游过载）。"""


RETRY_STATUSES = {429, 502, 503, 524}
# A rate-limited or overloaded upstream is a transient condition that states its own remedy, so
# the call is retried.  A single attempt made the retry branch below unreachable — the condition
# `attempt < RETRY_ATTEMPTS - 1` could never hold — which is why a 429 asking for a ten-second
# wait failed a whole source window instead.
RETRY_ATTEMPTS = 3
MAX_RETRY_WAIT = 60.0
# One bounded policy for empty replies: when the caller explicitly allows it, raise
# the output budget once while preserving the configured request parameters. A reply
# that is still empty ends the call instead of trying silent parameter variations.
EMPTY_RESPONSE_RECOVERIES = 1
# One generation step may issue at most this many worker requests in total, counting
# HTTP retries, empty-response recoveries, the plain-empty resend, the JSON correction
# and the deterministic validation correction.  A step can span more than one logical
# call, so the number is enforced by a RequestBudget shared by those calls rather than
# by each call separately.
MAX_REQUESTS_PER_STEP = 8


class ReplyBoundaryError(RuntimeError):
    """The upstream stopped at its output budget.

    The message already says what the client observed; `reply_state` carries the one
    fact the caller needs to describe the stop without parsing prose: ``empty`` when no
    content string came back at all, ``truncated`` when text did arrive but the reply
    was incomplete at the budget.
    """

    def __init__(self, message: str, *, reply_state: str) -> None:
        super().__init__(message)
        if reply_state not in {"empty", "truncated"}:
            raise ValueError(f"unknown reply state: {reply_state}")
        self.reply_state = reply_state


class RequestBudget:
    """One generation step's shared request cap and already-used recoveries.

    A generation step can issue several logical calls: the deterministic validation
    correction in `persistent_step`, and the plain-empty resend, the JSON correction and
    the empty-reply recoveries inside one `chat_json`.  The cap and the single budget
    raise therefore belong to the step; per call they would add up past the declared
    limit, and a later call would start again from a budget that already failed.

    Pass the same instance to every call of one step.  A new step gets a new instance,
    and a stored response that is reused never touches the counters.
    """

    def __init__(self, max_requests: int = MAX_REQUESTS_PER_STEP) -> None:
        self.max_requests = int(max_requests)
        self.requests = 0
        # A raise belongs to one stable prompt/window identity, not to the whole step:
        # sibling windows and nested leaves must each still start from the ordinary
        # budget, while the request count they share stays global.
        self.escalations: dict[str, int] = {}
        self.floors: dict[str, int] = {}
        self.escalated_to: int | None = None
        # Once an identity raised its budget, later calls for that identity start from at
        # least this value instead of retrying the budget that already failed.
        self.floor = 0
        self.recoveries: list[str] = []
        self.reasoning_fallback_used = False
        # Durable step ids that issued a request under this budget.  A parent step that
        # recovered through child windows can name them without pretending their
        # requests were its own.
        self.steps: list[str] = []

    def count(self) -> int:
        if self.requests >= self.max_requests:
            raise RuntimeError(
                f"本步骤已发出 {self.max_requests} 次模型请求（含 HTTP 重试、空响应恢复、"
                "重发、格式纠正与事实纠正），为避免无限重试已停止。"
                "已成功的步骤仍然保留，可从同一入口重试。"
            )
        self.requests += 1
        return self.requests

    def diagnostics(self) -> dict:
        """Snapshot the requests actually sent and the shared recovery state."""
        return {
            "step_requests": self.requests,
            "step_request_limit": self.max_requests,
            "step_recoveries": list(self.recoveries),
            "step_budget_floor": self.floor,
            "step_escalations": dict(self.escalations),
            "step_reasoning_fallback_used": self.reasoning_fallback_used,
            "step_ids": list(self.steps),
        }

    def floor_for(self, identity: str | None = None) -> int:
        """The smallest budget a request for this identity may start from."""
        return self.floors.get(identity or "", 0)

    def can_escalate(
        self, ceiling: int | None, budget: int, identity: str | None = None
    ) -> bool:
        key = identity or ""
        return bool(ceiling) and key not in self.escalations and budget < int(ceiling)

    def escalate(self, ceiling: int, budget: int, identity: str | None = None) -> int:
        key = identity or ""
        raised = int(ceiling)
        self.escalations[key] = raised
        self.floors[key] = max(self.floors.get(key, 0), raised)
        if not key:
            self.escalated_to = raised
            self.floor = self.floors[key]
        self.recoveries.append(f"把输出预算从 {budget} 提到 {raised}")
        return raised
# Seconds one upstream call may take when the caller does not say.  The worker applies the
# same default, so a change here has to be mirrored in llm_worker.py.
DEFAULT_READ_TIMEOUT = 240


def _retry_after(out: dict) -> float | None:
    """Read the wait an upstream asked for, when it states one.

    A rate limit arrives as {"error": {"message": ..., "retry_after": 10}} in the body, and no
    Retry-After header reaches the caller, so the body is where to look.
    """
    header = str(out.get("retry_after") or "").strip()
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    match = re.search(r'"retry_after"\s*:\s*([0-9]+(?:\.[0-9]+)?)', str(out.get("body") or ""))
    return float(match.group(1)) if match else None


def _retry_delay(attempt: int, status: int = 0, retry_after: float | None = None) -> float:
    if retry_after is not None:
        # Wait what the upstream asked for, within reason.
        return max(0.0, min(retry_after, MAX_RETRY_WAIT))
    # 524 是网关上游长请求超时（常因模型首 token 慢），多等一会再重试。
    base = 3.0 if status == 524 else 2.0
    return base * (attempt + 1)  # base*1..base*4

# ---------------- 工具函数 ----------------

def chat_url(base_url: str) -> str:
    """OpenAI 兼容 chat/completions 端点，健壮地处理各种 base_url 写法。

    - https://api.deepseek.com            → …/v1/chat/completions
    - https://api.deepseek.com/v1         → …/v1/chat/completions
    - https://open.bigmodel.cn/api/paas/v4 → …/paas/v4/chat/completions（不再多加 /v1）
    - …/chat/completions（用户误填完整端点）→ 原样使用
    """
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if re.search(r"/v\d+$", base):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def models_url(base_url: str) -> str:
    """对应 /models 列表端点，规则与 chat_url 一致。"""
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if re.search(r"/v\d+$", base):
        return base + "/models"
    return base + "/v1/models"


def list_models(cfg: dict) -> list[str]:
    """拉取该 base_url 下可用的模型 id 列表。"""
    url = models_url(cfg["base_url"])
    r = creq.get(url, impersonate="chrome", timeout=(10, 30), headers=auth_headers(cfg))
    try:
        r.raise_for_status()
    except Exception:  # noqa: BLE001
        raise RuntimeError(
            f"HTTP {r.status_code}\nURL: {url}\n响应: {r.text[:300]}"
        ) from None
    data = r.json()
    return [m["id"] for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]


def parse_json_result(text: str) -> tuple[dict, bool]:
    """Parse model output as JSON and say whether missing structure had to be closed.

    The second value is True when the object only parses after `_repair_truncated`
    dropped an incomplete tail and closed its containers.  Such a result is missing
    whatever the model had not written yet, so a caller that must not treat lost
    content as complete (an upstream that reported finish_reason=length) needs this
    signal; `parse_json` keeps the plain `-> dict` contract for everything else.
    """
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    if start == -1:
        raise ValueError(f"响应里没有 JSON 对象: {text[:200]!r}")
    candidate = text[start:]
    try:
        return json.loads(candidate), False
    except ValueError as first:
        repaired = _repair_truncated(candidate)
        if repaired is not None:
            try:
                return json.loads(repaired), True
            except ValueError:
                pass
        raise first


def parse_json(text: str) -> dict:
    """从模型输出中稳健地解析 JSON：容忍 ```json 围栏、前后缀文字，以及截断（自动修复失败则抛原始错误）。"""
    return parse_json_result(text)[0]


def _repair_truncated(s: str) -> str | None:
    """尽力修复被截断的 JSON：从最近的完整值处截短，补上缺失的闭合括号。

    只处理"流中断在结构中间"这一常见情况（如 timeline 数组未闭合）；
    结构严重错乱时返回 None，由上层走重发/报错。
    """
    n = len(s)
    if n < 2:
        return None
    # A response ending immediately after an opening container is not a
    # recoverable truncation: closing it would silently turn missing model
    # content into an empty result.
    if re.search(r"[\[{]\s*$", s):
        return None
    cuts = [n]
    for m in re.finditer(r"[,\{\[\]}]", s):
        cuts.append(m.end())
    for cut in sorted(set(cuts), reverse=True)[:40]:
        piece = s[:cut]
        stack: list[str] = []
        in_str = False
        i = 0
        ok = True
        while i < len(piece):
            ch = piece[i]
            if in_str:
                if ch == "\\":
                    i += 2
                    continue
                if ch == '"':
                    in_str = False
                i += 1
                continue
            if ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append(ch)
            elif ch in "}]":
                if not stack:
                    ok = False
                    break
                top = stack.pop()
                if (top == "{" and ch != "}") or (top == "[" and ch != "]"):
                    ok = False
                    break
            i += 1
        if not ok or in_str:
            continue
        closer = "".join("}" if c == "{" else "]" for c in reversed(stack))
        try:
            json.loads(piece + closer)  # 验证可解析
            return piece + closer  # 返回修复后的字符串
        except ValueError:
            continue
    return None


def find_task(messages: list[dict]) -> str:
    """在消息中查找 [TASK:xxx] 标记，供假客户端使用。"""
    for m in messages:
        for hit in re.findall(r"\[TASK:([\w:.-]+)\]", str(m.get("content", ""))):
            return hit
    return ""

# ---------------- 真实客户端 ----------------

class LLMClient:
    """OpenAI 兼容客户端。用 curl_cffi 模拟 Chrome TLS 指纹，规避 Cloudflare 反脚本检测。"""

    def __init__(self, cfg: dict):
        self.base_url = cfg["base_url"].rstrip("/")
        self.api_key = cfg["api_key"]
        self.model = cfg["model"]
        # Optional; empty means the parameter is not sent, because an upstream that does
        # not recognise a value rejects the whole request with HTTP 400.
        self.reasoning_effort = str(cfg.get("reasoning_effort") or "")
        # Why the last reply could be empty; see chat().
        self.last_finish_reason: str | None = None
        # Non-sensitive request summaries for the most recent logical step: effective
        # budget and effort, finish_reason, content length, upstream usage when it
        # reported one, and duration. Never prompts, keys, headers or endpoint URLs.
        self.last_request_diagnostics: list[dict] = []
        self.last_recovery_actions: list[str] = []
        # The generation step currently in progress, or None for a standalone call.
        self._budget: RequestBudget | None = None

    def _url(self) -> str:
        return chat_url(self.base_url)

    def _headers(self) -> dict:
        return auth_headers({"api_key": self.api_key})

    def _worker_call(self, messages: list[dict], temperature: float,
                     max_tokens: int, stream: bool = False,
                     request_timeout: int | float | None = None) -> dict:
        """在独立子进程里用 curl_cffi 发请求（主线程可靠 + subprocess 硬超时）。"""
        payload = {
            "base_url": self.base_url, "api_key": self.api_key, "model": self.model,
            "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
            "stream": stream, "reasoning_effort": self.reasoning_effort,
        }
        if request_timeout is not None:
            payload["request_timeout"] = request_timeout
        script = Path(__file__).with_name("llm_worker.py")
        # The parent must outwait the worker.  The worker allows itself
        # max(request_timeout or DEFAULT_READ_TIMEOUT) seconds to read the reply, so a
        # shorter parent timeout kills a call that is still inside its own budget and
        # reports a timeout the upstream never caused.  A 120-second default here did
        # exactly that to every long analysis request.
        subprocess_timeout = max(float(request_timeout or DEFAULT_READ_TIMEOUT), 1.0) + 30.0
        proc = subprocess.run(
            [sys.executable, str(script)], input=json.dumps(payload),
            capture_output=True, text=True, timeout=subprocess_timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        raw = (proc.stdout or "").strip()
        try:
            result = json.loads(raw or "{}")
        except ValueError:
            detail = raw[:300] or (proc.stderr or "").strip()[:300] or "没有可解析的 worker 输出"
            return {
                "error": f"worker 输出异常: {detail}",
                "kind": "worker",
                "status": None,
                "body": detail,
                "error_type": "WorkerOutputError",
            }
        if proc.returncode != 0 and not result.get("error"):
            detail = (proc.stderr or raw or "worker 子进程异常").strip()[:500]
            return {
                "error": f"worker 子进程退出码 {proc.returncode}: {detail}",
                "kind": "worker",
                "status": None,
                "body": detail,
                "error_type": "WorkerProcessError",
            }
        return result

    def _format_worker_error(self, out: dict) -> str:
        """Turn a worker failure into an actionable message without fake HTTP codes."""
        kind = out.get("kind")
        status = out.get("status")
        body = str(out.get("body") or "").strip()
        detail = body or str(out.get("error") or "未知错误").strip()
        # Older workers persisted a missing status as ``0``. Treat it as a
        # transport failure instead of presenting a fictitious HTTP response.
        if kind == "http" and status in (None, 0, "0", ""):
            kind = "network"
        if kind == "network":
            return f"API 网络连接失败：未收到 HTTP 响应\nURL: {self._url()}\n详情: {detail}"
        if kind == "timeout":
            timeout_label = out.get("timeout") or "请求"
            return f"API 请求超时（{timeout_label}）\nURL: {self._url()}\n详情: {detail}"
        if kind == "worker":
            return f"API worker 执行失败：{detail}\nURL: {self._url()}"
        if kind == "response":
            return f"API 响应解析失败\nURL: {self._url()}\n详情: {detail}"
        if kind == "http" and status == 403:
            account_muted = "account_muted" in detail.casefold()
            label = "API 账号访问被暂停（account_muted）" if account_muted else "API 访问被拒绝（HTTP 403）"
            return (
                f"{label}\nURL: {self._url()}\n"
                "请检查账号、密钥、代理或供应商状态；该错误不会自动重试。\n"
                f"响应: {detail}"
            )
        status_label = str(status) if status is not None else "未知"
        return f"API 请求失败 HTTP {status_label}\nURL: {self._url()}\n响应: {detail}"

    def _count_request(self) -> None:
        """Count one worker request against the current step budget."""
        if getattr(self, "_budget", None) is None:
            # A direct _chat_request call outside chat()/chat_json() still needs a
            # counter, so give it a throwaway one.
            self._budget = RequestBudget()
        self._budget.count()

    def _record_diagnostics(self, out: dict | None, *, budget: int, started: float,
                            request_timeout: int | float | None, outcome: str) -> None:
        """Append one non-sensitive summary of a worker request.

        Only what is needed to distinguish a returned empty/truncated response from a
        transport failure: the effective budget and effort, finish_reason,
        content/reasoning lengths, tool/refusal presence, the usage the upstream
        reported (absent when it reported none) and how long it took. Prompts, keys,
        headers and URLs never enter here.
        """
        out = out or {}
        content = out.get("content")
        length = out.get("content_length")
        if not isinstance(length, int):
            length = len(content) if isinstance(content, str) else None
        usage = out.get("usage")
        self.last_request_diagnostics.append({
            "index": len(self.last_request_diagnostics) + 1,
            "max_tokens": budget,
            "reasoning_effort": self.reasoning_effort,
            "request_timeout": request_timeout,
            "outcome": outcome,
            "finish_reason": out.get("finish_reason"),
            "content_length": length,
            "reasoning_content_present": bool(out.get("reasoning_content_present")),
            "reasoning_content_length": out.get("reasoning_content_length"),
            "refusal_present": bool(out.get("refusal_present")),
            "tool_call_count": out.get("tool_call_count", 0),
            "usage": usage if isinstance(usage, dict) else None,
            "status": out.get("status"),
            "kind": out.get("kind"),
            "duration_ms": int(round((time.monotonic() - started) * 1000)),
        })

    def _chat_request(self, messages: list[dict], temperature: float, budget: int,
                      request_timeout: int | float | None) -> tuple[str, str | None]:
        """Issue one content request, keeping the existing HTTP retry behaviour."""
        for attempt in range(RETRY_ATTEMPTS):
            self._count_request()
            started = time.monotonic()
            try:
                out = self._worker_call(
                    messages,
                    temperature,
                    budget,
                    stream=False,
                    request_timeout=request_timeout,
                )
            except subprocess.TimeoutExpired:
                self._record_diagnostics(
                    None, budget=budget, started=started,
                    request_timeout=request_timeout, outcome="timeout",
                )
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(_retry_delay(attempt))
                    continue
                timeout_label = (
                    request_timeout if request_timeout is not None else DEFAULT_READ_TIMEOUT
                )
                raise RuntimeError(
                    f"API 请求超时（{timeout_label}s）\nURL: {self._url()}"
                )
            if out.get("error"):
                status = out.get("status")
                retryable = status in RETRY_STATUSES and attempt < RETRY_ATTEMPTS - 1
                self._record_diagnostics(
                    out, budget=budget, started=started,
                    request_timeout=request_timeout,
                    outcome="http_retry" if retryable else "error",
                )
                if retryable:
                    time.sleep(_retry_delay(attempt, status or 0, _retry_after(out)))
                    continue
                raise RuntimeError(self._format_worker_error(out))
            content = out.get("content")
            self.last_finish_reason = out.get("finish_reason")
            # Some upstreams answer with a null content field — a reasoning-only or
            # tool-call-only message.  Treat it as empty text; returning None made
            # callers crash on None.strip() instead of reporting an empty reply.
            text = "" if content is None else str(content)
            self._record_diagnostics(
                out, budget=budget, started=started,
                request_timeout=request_timeout,
                outcome="content" if text.strip() else "empty",
            )
            return text, self.last_finish_reason
        raise RuntimeError("chat: unreachable")

    def chat(self, messages: list[dict], temperature: float = 0.3,
             max_tokens: int = 8000,
             request_timeout: int | float | None = None,
             escalate_max_tokens: int | None = None) -> str:
        """Send one content request and return its text (empty when the upstream gave none).

        Empty replies are recovered by ONE bounded policy: when the upstream reported
        finish_reason=length, the client may raise the budget once if the caller
        explicitly allows it through ``escalate_max_tokens``. It preserves the
        configured reasoning hint and all other request parameters. There is no global
        ceiling, silent parameter switching, or endless parameter juggling; a reply
        that is still empty ends the call.

        The request cap and the single budget raise belong to the current generation
        step (see RequestBudget): chat_json() supplies that step, while a standalone
        chat() gets a fresh one.  A call inside a step that already raised the budget
        starts from the raised value instead of the budget that already failed.
        """
        owned = getattr(self, "_budget", None) is None
        state = RequestBudget() if owned else self._budget
        if owned:
            self._budget = state
            self.last_request_diagnostics = []
        self.last_recovery_actions = state.recoveries
        identity = getattr(self, "_budget_identity", None)
        budget = max(int(max_tokens), state.floor_for(identity))
        try:
            for recovery in range(EMPTY_RESPONSE_RECOVERIES + 1):
                text, finish = self._chat_request(
                    messages, temperature, budget, request_timeout
                )
                if text.strip():
                    return text
                if finish != "length" or recovery == EMPTY_RESPONSE_RECOVERIES:
                    return text
                if state.can_escalate(escalate_max_tokens, budget, identity):
                    budget = state.escalate(escalate_max_tokens, budget, identity)
                    continue
                return text
            return ""
        finally:
            if owned:
                self._budget = None

    def chat_streamed(self, messages: list[dict], **kw) -> str:
        """用流式接口接收完整文本再返回（规避 524：只要持续有 token，网关不会判超时）。"""
        return "".join(self.stream(messages, **kw))

    def _reply_facts(self) -> str:
        """Describe the last request from facts the client actually observed.

        It states what the upstream reported, which budget and request hint that call
        used, which recoveries already ran and that the client stopped. It reports
        reasoning-content metadata when the upstream exposed that field, without
        including the content. Usage is whatever the upstream reported; without a
        report it stays unknown rather than inferred.
        """
        last = self.last_request_diagnostics[-1] if self.last_request_diagnostics else {}
        parts = []
        finish = last.get("finish_reason")
        parts.append(
            f"finish_reason={finish}" if finish else "上游没有报告 finish_reason"
        )
        if last.get("max_tokens") is not None:
            effort = last.get("reasoning_effort") or "未设置"
            parts.append(
                f"本次请求实际发出的 max_tokens={last.get('max_tokens')}、"
                f"reasoning_effort={effort}"
            )
        if last.get("content_length") is not None:
            parts.append(f"返回正文长度 {last.get('content_length')} 字符")
        if last.get("reasoning_content_present"):
            reasoning_length = last.get("reasoning_content_length")
            if reasoning_length is None:
                parts.append("上游返回 reasoning_content（长度未知）")
            else:
                parts.append(f"上游返回 reasoning_content，长度 {reasoning_length} 字符")
        if last.get("refusal_present"):
            parts.append("上游返回了 refusal 字段")
        if last.get("tool_call_count"):
            parts.append(f"上游返回工具调用 {last.get('tool_call_count')} 个")
        usage = last.get("usage")
        if isinstance(usage, dict):
            parts.append(
                "上游报告的用量：" + "、".join(f"{key}={value}" for key, value in usage.items())
            )
        else:
            parts.append("上游没有报告用量（未知）")
        actions = "、".join(self.last_recovery_actions) or "没有可用的恢复手段"
        parts.append(f"已经尝试的恢复：{actions}")
        budget = getattr(self, "_budget", None)
        issued = budget.requests if budget else len(self.last_request_diagnostics)
        parts.append(
            f"本步骤共发出 {issued} 次请求，现已停止，不再继续重试或调整参数"
        )
        return "；".join(parts)

    def _empty_reply_message(self) -> str:
        return (
            "上游返回了空正文，并报告 " + self._reply_facts()
            + "。已成功的步骤会保留，可从同一入口重试。"
        )

    def _truncated_reply_message(self) -> str:
        return (
            "上游返回了正文并报告 finish_reason=length，正文在预算处被截断：返回的 JSON 只有"
            "补齐缺失结构后才能解析，缺失的那部分内容无法确认，因此没有把这次补全结果当作"
            "完整成功；" + self._reply_facts() + "。已成功的步骤会保留，可从同一入口重试。"
        )

    def _try_reasoning_effort_fallback(
        self, fallback: str | None, configured: str
    ) -> bool:
        state = getattr(self, "_budget", None)
        if (
            not fallback
            or configured
            or state is None
            or state.reasoning_fallback_used
        ):
            return False
        # Some reasoning models consume the default output budget before writing
        # complete JSON. Only an opted-in caller may switch; explicit user settings
        # remain authoritative.
        self.reasoning_effort = fallback
        state.reasoning_fallback_used = True
        state.recoveries.append(f"将 reasoning_effort 从未设置调整为 {fallback}")
        return True

    def chat_json(self, messages: list[dict], step_budget: RequestBudget | None = None,
                  budget_identity: str | None = None, **kw) -> dict:
        """调用模型并解析 JSON；空响应/解析失败各重发一次。

        The whole call is one logical step: every request below — HTTP retries, the
        empty-reply recoveries, the plain-empty resend, the JSON correction and the
        budget raise — shares one `RequestBudget`.  Callers that continue the same
        generation step (the deterministic validation correction in `persistent_step`)
        pass its budget in, so the cap and the single raise span the whole step.
        """
        # A few offline callers replace ``chat`` on a deliberately lightweight
        # instance created with ``__new__``.  Keep that test seam compatible with
        # the normal constructor while still resetting per-call diagnostics below.
        if not hasattr(self, "reasoning_effort"):
            self.reasoning_effort = ""
        if not hasattr(self, "last_finish_reason"):
            self.last_finish_reason = None
        previous = getattr(self, "_budget", None)
        previous_identity = getattr(self, "_budget_identity", None)
        state = step_budget if step_budget is not None else RequestBudget()
        self._budget = state
        self._budget_identity = budget_identity
        self.last_finish_reason = None
        self.last_request_diagnostics = []
        self.last_recovery_actions = state.recoveries
        try:
            return self._chat_json(messages, **kw)
        finally:
            self._budget = previous
            self._budget_identity = previous_identity

    def _chat_json(self, messages: list[dict], **kw) -> dict:
        working_messages = list(messages)
        plain_empty_retried = False
        correction_sent = False
        truncation_retried = False
        fallback_reasoning_effort = kw.pop("fallback_reasoning_effort", None)
        if fallback_reasoning_effort is not None:
            fallback_reasoning_effort = str(fallback_reasoning_effort).strip()
            if fallback_reasoning_effort not in {"low", "medium", "high"}:
                raise ValueError("fallback_reasoning_effort 只支持 low / medium / high")
        configured_reasoning_effort = self.reasoning_effort
        # Every branch below either returns, raises, or consumes one of the three
        # one-shot flags; the request budget bounds the rest.
        try:
            while True:
                # 非流式：curl_cffi 流式在 uvicorn 线程里会卡，非流式稳定
                text = self.chat(working_messages, **kw)
                if not text.strip():
                    if (
                        self.last_finish_reason == "length"
                        and self._try_reasoning_effort_fallback(
                            fallback_reasoning_effort, configured_reasoning_effort
                        )
                    ):
                        continue
                    if self.last_finish_reason == "length":
                        # The bounded recovery in chat() already ran (one explicit budget
                        # raise when allowed), so another resend would repeat the same
                        # request. Report what was observed instead.
                        raise ReplyBoundaryError(
                        self._empty_reply_message(), reply_state="empty"
                    )
                    if plain_empty_retried:
                        budget = getattr(self, "_budget", None)
                        issued_requests = budget.requests if budget else 0
                        raise RuntimeError(
                            "模型连续两次返回空响应（上游没有返回正文，也没有报告 "
                            "finish_reason=length）。"
                            f"本步骤共发出 "
                            f"{issued_requests} 次请求，现已停止。"
                            "请检查 base_url / 模型名 / 中转站是否可用；已成功的步骤会保留，"
                            "可从同一入口重试。"
                        )
                    plain_empty_retried = True
                    continue
                try:
                    parsed, repaired = parse_json_result(text)
                except (json.JSONDecodeError, ValueError) as e:
                    last = "JSONDecodeError" if isinstance(e, json.JSONDecodeError) else "ValueError"
                    tail = text[-300:] if text else "(empty)"
                    reason = "模型未返回 JSON 对象" if "{" not in text else "JSON 语法无效或内容不完整"
                    msg = f"{reason}（{last}: {e}）。\n末尾 {len(tail)} 字符: ...{tail!r}"
                    if self.last_finish_reason == "length":
                        # Different from an empty reply: the upstream DID send content, but
                        # reported that it stopped at length, so the tail is probably cut.
                        budget = (
                            self.last_request_diagnostics[-1].get("max_tokens")
                            if self.last_request_diagnostics else "未知"
                        )
                        msg += (
                            "\n上游报告 finish_reason=length，正文可能在预算处被截断。"
                            f"本次请求实际发出的 max_tokens={budget}。"
                        )
                        if self._try_reasoning_effort_fallback(
                            fallback_reasoning_effort, configured_reasoning_effort
                        ):
                            continue
                    if correction_sent:
                        if self.last_finish_reason == "length":
                            raise ReplyBoundaryError(
                                msg, reply_state="truncated"
                            ) from e
                        raise RuntimeError(msg) from e
                    working_messages = [*messages, {
                        "role": "user",
                        "content": (
                            "Your previous response contained no JSON object. "
                            if "{" not in text else
                            "Your previous response contained invalid or incomplete JSON. "
                        ) + "Return the complete JSON object following the original schema, "
                        "with exact field names. No headings, Markdown, or explanatory prose.",
                    }]
                    msg += "\n将重发一次请求。"
                    print(msg, flush=True)
                    correction_sent = True
                    continue
                if repaired and self.last_finish_reason == "length":
                    # The reply only parses because missing structure was closed up, so the
                    # model never wrote part of the answer.  Treating that repair as a
                    # success is how lost content was stored as a completed step.  Retry
                    # once at the raised budget when the caller allows one, otherwise fail.
                    budget = (
                        self.last_request_diagnostics[-1].get("max_tokens")
                        if self.last_request_diagnostics else None
                    )
                    ceiling = kw.get("escalate_max_tokens")
                    state = getattr(self, "_budget", None)
                    # The raise belongs to this prompt's identity, exactly as in chat();
                    # without it the state would land under the empty identity and the
                    # retry would silently send the original budget again.
                    identity = getattr(self, "_budget_identity", None)
                    if self._try_reasoning_effort_fallback(
                        fallback_reasoning_effort, configured_reasoning_effort
                    ):
                        continue
                    if (
                        not truncation_retried
                        and state is not None
                        and budget is not None
                        and state.can_escalate(ceiling, budget, identity)
                    ):
                        truncation_retried = True
                        state.escalate(ceiling, budget, identity)
                        continue
                    raise ReplyBoundaryError(
                    self._truncated_reply_message(), reply_state="truncated"
                )
                return parsed
        finally:
            self.reasoning_effort = configured_reasoning_effort

    def stream(self, messages: list[dict], temperature: float = 0.4,
               max_tokens: int = 8000,
               request_timeout: int | float | None = None) -> Iterator[str]:
        """兼容接口：子进程一次性返回全文（token 级流式由前端进度替代）。"""
        yield self.chat(
            messages,
            temperature,
            max_tokens,
            request_timeout=request_timeout,
        )



FAKE_OUTPUTS: dict[str, dict] = {}


def _fake_segment_output(messages: list[dict]) -> dict:
    content = "\n".join(str(message.get("content", "")) for message in messages)
    refining = find_task(messages) == "prep:segment:refine"
    match = re.search(r"^SELECTED_PAGES_JSON=(.+)$", content, re.MULTILINE)
    pages = json.loads(match.group(1)) if match else [1]
    if not pages:
        return {"segments": []}
    segments = []
    start = previous = int(pages[0])
    for raw_page in pages[1:]:
        page = int(raw_page)
        if page != previous + 1 or (refining and previous - start + 1 >= 3):
            segments.append({"start": start, "end": previous, "label": "离线语义段"})
            start = page
        previous = page
    segments.append({"start": start, "end": previous, "label": "离线语义段"})
    return {"segments": segments}


def _fake_prep_output(messages: list[dict]) -> dict:
    content = "\n".join(str(message.get("content", "")) for message in messages)
    file_match = re.search(r"^SOURCE_FILE_JSON=(.+)$", content, re.MULTILINE)
    pages_match = re.search(r"^SOURCE_PAGES_JSON=(.+)$", content, re.MULTILINE)
    core_pages_match = re.search(r"^CORE_PAGES_JSON=(.+)$", content, re.MULTILINE)
    source_match = re.search(
        r"SOURCE_TEXT_START\n(.*?)\nSOURCE_TEXT_END", content, re.DOTALL
    )
    source_file = json.loads(file_match.group(1)) if file_match else "fixture://fake-prep"
    pages = json.loads(pages_match.group(1)) if pages_match else [1]
    core_pages = json.loads(core_pages_match.group(1)) if core_pages_match else pages
    source_text = source_match.group(1) if source_match else ""
    page_blocks = {
        int(page): text.strip()
        for page, text in re.findall(
            r"--- PDF p(\d+) ---\n(.*?)(?=\n\n--- PDF p\d+ ---|\Z)",
            source_text,
            re.DOTALL,
        )
    }
    cited_pages = [core_pages[0]]
    if pages[-1] != core_pages[0]:
        cited_pages.append(pages[-1])
    source_refs = []
    text_parts = []
    for page in cited_pages:
        block = page_blocks.get(page, "")
        excerpt = " ".join(line.strip() for line in block.splitlines() if line.strip())
        excerpt = excerpt[:240] or f"PDF p{page}"
        source_refs.append(
            {
                "file": source_file,
                "page": page,
                "excerpt": excerpt,
                "locator": "FakeLLM workflow fixture",
            }
        )
        text_parts.append(excerpt)
    return {
        "candidates": [
            {
                "text": " / ".join(text_parts)[:1200],
                "kind": "event",
                "source_refs": source_refs,
                "confidence": 0.5,
                "possible_links": [],
                "open_questions": [
                    "FakeLLM only verifies the local workflow; review the cited pages."
                ],
            }
        ]
    }


def _fake_consolidation_output(messages: list[dict]) -> dict:
    """Keep the offline workflow lossless while exercising segment reduction."""
    content = "\n".join(str(message.get("content", "")) for message in messages)
    candidates_match = re.search(
        r"^WINDOW_CANDIDATES_JSON=(.+)$", content, re.MULTILINE
    )
    raw_candidates = json.loads(candidates_match.group(1)) if candidates_match else []
    if not isinstance(raw_candidates, list):
        return {"candidates": []}

    merged: list[dict] = []
    by_key: dict[tuple[str, str], dict] = {}
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            continue
        candidate = dict(raw)
        candidate.pop("candidate_id", None)
        key = (
            str(candidate.get("kind", "clue")),
            " ".join(str(candidate.get("text", "")).split()).casefold(),
        )
        if not key[1]:
            continue
        existing = by_key.get(key)
        if existing is None:
            candidate["source_refs"] = list(candidate.get("source_refs") or [])
            candidate["possible_links"] = list(candidate.get("possible_links") or [])
            candidate["open_questions"] = list(candidate.get("open_questions") or [])
            by_key[key] = candidate
            merged.append(candidate)
            continue
        for field in ("source_refs", "possible_links", "open_questions"):
            values = [*existing.get(field, []), *candidate.get(field, [])]
            existing[field] = list(
                dict.fromkeys(json.dumps(item, sort_keys=True, ensure_ascii=False) if field == "source_refs" else str(item) for item in values)
            )
            if field == "source_refs":
                existing[field] = [json.loads(item) for item in existing[field]]
    return {"candidates": merged}


def _fake_artifact_output(messages: list[dict]) -> dict:
    content = "\n".join(str(message.get("content", "")) for message in messages)
    profile_match = re.search(r"^BOARD_PROFILE_JSON=(.+)$", content, re.MULTILINE)
    facts_match = re.search(r"^REVIEWED_FACTS_JSON=(.+)$", content, re.MULTILINE)
    profile = json.loads(profile_match.group(1)) if profile_match else {}
    facts = json.loads(facts_match.group(1)) if facts_match else []
    if not facts:
        return {"cards": [], "open_questions": ["FakeLLM found no reviewed facts."]}
    list_fields = {
        "immediate_actions", "direct_clues", "hidden_clues", "gm_moves",
        "exit_conditions", "npc_hooks", "clock_links", "noncombat_exits",
        "stages", "usable_features", "hazards", "positioning",
        "environment_changes", "signature_actions", "resolutions",
        "entry_points", "discoveries", "relevant_characters",
        "major_conflicts", "opening_conditions", "possible_endings",
        "key_people", "factions", "cross_environment_consequences",
        "impulses", "gm_responses", "phase_changes", "exit_outcomes",
        "triggers", "potential_adversaries", "motives", "attitude_changes",
        "choices", "complications",
    }
    fact_ids = [item["id"] for item in facts[:3]]
    source_text = " / ".join(item["text"] for item in facts[:3])[:600]
    cards = []
    for index, definition in enumerate(profile.get("card_definitions", []), start=1):
        fields = {}
        field_sources = {}
        for field_name in definition.get("required_fields", []):
            fields[field_name] = [source_text] if field_name in list_fields else source_text
            field_sources[field_name] = list(fact_ids)
        cards.append(
            {
                "type": definition["type"],
                "title": f"FakeLLM {definition.get('display_name') or definition['type']} {index}",
                "subtitle": "离线工作流验证草案",
                "fact_ids": list(fact_ids),
                "fields": fields,
                "field_sources": field_sources,
                "open_questions": ["仅用于验证生成、审批与场景解锁流程。"],
            }
        )
    return {"cards": cards, "open_questions": []}


def _fake_local_digest_output(messages: list[dict]) -> dict:
    content = "\n".join(str(message.get("content", "")) for message in messages)
    facts_match = re.search(r"^LOCAL_FACTS_JSON=(.+)$", content, re.MULTILINE)
    facts = json.loads(facts_match.group(1)) if facts_match else []
    kind_map = {
        "clue": "clue_cluster",
        "npc": "npc",
        "location": "location",
        "event": "scene",
        "threat": "threat",
        "stakes": "background",
        "obstacle": "scene",
        "timeline": "timeline",
        "resource": "resource",
    }
    grouped: dict[str, list[dict]] = {}
    location_items: list[dict] = []
    for fact in facts:
        kind = kind_map.get(fact.get("kind"), "background")
        if kind == "location":
            location_items.append(fact)
        else:
            grouped.setdefault(kind, []).append(fact)
    units = []
    for fact in location_items:
        title = str(fact.get("text") or "离线地点").strip()[:80]
        units.append(
            {
                "kind": "location",
                "title": title,
                "summary": str(fact.get("text") or "离线地点索引")[:700],
                "fact_ids": [fact["id"]],
                "entity_keys": [title],
                "relationship_hints": [],
                "open_questions": [],
            }
        )
    for kind, items in grouped.items():
        sample = " / ".join(str(item.get("text", "")) for item in items[:2])[:700]
        units.append(
            {
                "kind": kind,
                "title": f"离线局部整理 {len(units) + 1}",
                "summary": sample or "离线局部整理索引",
                "fact_ids": [item["id"] for item in items],
                "entity_keys": [],
                "relationship_hints": [],
                "open_questions": [],
            }
        )
    return {"units": units, "open_questions": []}


def _fake_leaf_plan_output(messages: list[dict]) -> dict:
    """The fake answer for a leaf window, mirroring the production contract.

    It picks the board's anchoring card type, honours the chapter scope the request
    supplied (chapter ids and chapter_briefs), returns the package brief an
    environment-led workspace without chapters needs, and adds the partial overview card
    the contract asks for.  A fake run therefore cannot satisfy a contract it never
    produced, and the merged parent validation sees the same shape a real leaf owes.
    """
    content = "\n".join(str(message.get("content", "")) for message in messages)
    units_match = re.search(r"^GLOBAL_UNITS_JSON=(.+)$", content, re.MULTILINE)
    types_match = re.search(r"^ALLOWED_CARD_TYPES_JSON=(.+)$", content, re.MULTILINE)
    structure_match = re.search(r"^REVIEWED_STRUCTURE_JSON=(.+)$", content, re.MULTILINE)
    units = json.loads(units_match.group(1)) if units_match else []
    types = json.loads(types_match.group(1)) if types_match else ["scene"]
    try:
        structure = json.loads(structure_match.group(1)) if structure_match else None
    except ValueError:
        structure = None
    chapters = [
        chapter.get("id")
        for chapter in (structure or {}).get("chapters", [])
        if isinstance(chapter, dict) and chapter.get("id")
    ]
    anchor = next(
        (name for name in ("location", "environment", "scene", "scene_extract")
         if name in types),
        next((name for name in types if name != "chapter_overview"), types[0]),
    )
    fact_ids = [fact_id for unit in units for fact_id in (unit.get("fact_ids") or [])]
    cards = [
        {
            "type": anchor,
            "title": unit.get("title") or "规划卡",
            "purpose": "保留当前窗口可调用的入口。",
            "fact_ids": list(unit.get("fact_ids") or []),
            "chapter_ids": list(chapters),
            "focus": [],
            "open_questions": [],
            "runtime_groups": [],
            "story_beat_ids": [],
            "situation": "",
            "case_scope": "current",
        }
        for unit in units
        if unit.get("fact_ids")
    ]
    if "chapter_overview" in types and fact_ids:
        cards.append(
            {
                "type": "chapter_overview",
                "title": "窗口概览",
                "purpose": "概述本窗口事实在整体情势中的位置。",
                "fact_ids": fact_ids,
                "chapter_ids": list(chapters),
            }
        )
    payload: dict = {"cards": cards, "open_questions": []}
    if chapters:
        payload["chapter_briefs"] = [
            {
                "chapter_id": chapter_id,
                "brief": {"premise": "前提", "current_situation": "现状"},
                "reference_fact_ids": fact_ids,
            }
            for chapter_id in chapters
        ]
    elif "environment" in types:
        payload["brief"] = {"premise": "前提", "current_situation": "现状"}
    return payload


def _fake_global_plan_output(messages: list[dict]) -> dict:
    content = "\n".join(str(message.get("content", "")) for message in messages)
    profile_match = re.search(r"^BOARD_PROFILE_JSON=(.+)$", content, re.MULTILINE)
    units_match = re.search(r"^GLOBAL_UNITS_JSON=(.+)$", content, re.MULTILINE)
    sizes_match = re.search(r"^FACT_INPUT_CHARS_JSON=(.+)$", content, re.MULTILINE)
    tokens_match = re.search(r"^FACT_INPUT_TOKENS_JSON=(.+)$", content, re.MULTILINE)
    budget_match = re.search(r"^MAX_CARD_FACT_INPUT_CHARS_JSON=(\d+)$", content, re.MULTILINE)
    token_budget_match = re.search(r"^MAX_CARD_FACT_INPUT_TOKENS_JSON=(\d+)$", content, re.MULTILINE)
    structure_match = re.search(r"^REVIEWED_STRUCTURE_JSON=(.+)$", content, re.MULTILINE)
    profile = json.loads(profile_match.group(1)) if profile_match else {}
    units = json.loads(units_match.group(1)) if units_match else []
    sizes = json.loads(sizes_match.group(1)) if sizes_match else {}
    tokens = json.loads(tokens_match.group(1)) if tokens_match else {}
    budget = int(budget_match.group(1)) if budget_match else 72_000
    token_budget = int(token_budget_match.group(1)) if token_budget_match else 22_000
    structure = json.loads(structure_match.group(1)) if structure_match else None
    chapters = structure.get("chapters", []) if isinstance(structure, dict) else []
    chapter_ids = [item["id"] for item in chapters]
    fact_ids = list(
        dict.fromkeys(
            fact_id for unit in units for fact_id in unit.get("fact_ids", [])
        )
    )
    chunks: list[list[str]] = []
    current: list[str] = []
    current_size = 0
    current_tokens = 0
    for fact_id in fact_ids:
        fact_size = max(int(sizes.get(fact_id, 1)), 1)
        fact_tokens = max(int(tokens.get(fact_id, 1)), 1)
        if current and (
            len(current) >= 90
            or current_size + fact_size > budget - 1000
            or current_tokens + fact_tokens > token_budget - 500
        ):
            chunks.append(current)
            current = []
            current_size = 0
            current_tokens = 0
        current.append(fact_id)
        current_size += fact_size
        current_tokens += fact_tokens
    if current:
        chunks.append(current)

    definitions = profile.get("card_definitions", [])
    cards = []
    # The scope states the facts it must account for directly; the per-fact
    # disposition records this once read cost more than a large chapter's whole
    # planning budget and are no longer sent.
    scope_fact_ids = (
        structure.get("chapter_fact_ids", []) if isinstance(structure, dict) else []
    )
    chapter_briefs = []
    for chapter in chapters:
        applicable = list(scope_fact_ids)
        chapter_briefs.append(
            {
                "chapter_id": chapter["id"],
                "brief": {
                    "premise": chapter["summary"],
                    "current_situation": "该叙事阶段包含可回应的局势。",
                    "major_conflicts": ["推进当前阶段的主要目标。"],
                    "opening_conditions": ["从已确认事实进入该阶段。"],
                    "possible_endings": ["局势转入后续阶段。"],
                    "fact_ids": applicable[: min(20, len(applicable))],
                    "field_sources": {},
                    "open_questions": [],
                },
                "reference_fact_ids": applicable,
            }
        )
    # Every fact in a chapter scope is chapter-owned, so nothing is a global reference.
    global_references: list[str] = []
    runtime_brief = (
        {
            "premise": "离线测试用运行包，围绕所选事实组织可运行局势。",
            "current_situation": "局势已进入可回应的开场状态。",
            "major_conflicts": ["在有限信息与不断推进的压力下作出选择。"],
            "opening_conditions": ["冒险者已抵达第一个可回应的环境。"],
            "possible_endings": ["局势被扭转", "压力继续向下一环境推进"],
            "fact_ids": fact_ids[: min(len(fact_ids), 20)],
            "field_sources": {},
            "open_questions": [],
        }
        if profile.get("profile_kind") == "runtime"
        else None
    )
    if profile.get("profile_kind") == "runtime" and len(fact_ids) <= 90:
        unit_facts: dict[str, list[str]] = {}
        for unit in units:
            unit_facts.setdefault(str(unit.get("kind") or "background"), []).extend(unit.get("fact_ids", []))
        type_units = {
            "location": ("location", "scene"),
            "chapter_overview": tuple(unit_facts),
            "npc": ("npc",),
            "threat": ("threat",),
            "clock": ("timeline", "outcome"),
        }
        for index, definition in enumerate(definitions, start=1):
            if definition["type"] == "location":
                location_units = [unit for unit in units if unit.get("kind") == "location"]
                if location_units:
                    for location_index, unit in enumerate(location_units, start=1):
                        cards.append({
                            "type": "location",
                            "title": str(unit.get("title") or f"FakeLLM 地点 {location_index}"),
                            "purpose": "独立验证一个可进入、调查或折返的地点。",
                            "fact_ids": list(dict.fromkeys(unit.get("fact_ids", []))),
                            "focus": list(dict.fromkeys(unit.get("entity_keys", []))),
                            "open_questions": [],
                        })
                    continue
            relevant = list(dict.fromkeys(
                fact_id
                for kind in type_units.get(definition["type"], ())
                for fact_id in unit_facts.get(kind, [])
            ))
            if not relevant:
                relevant = fact_ids[:1]
            runtime_groups = []
            if profile.get("runtime_navigation_mode") == "environment" and definition["type"] in {
                "environment", "npc", "enemy", "encounter_clock"
            }:
                runtime_groups = ["主要局势"]
            cards.append({
                "type": definition["type"],
                "title": f"FakeLLM {definition.get('display_name') or definition['type']} {index}",
                "purpose": "在离线流程中验证完整运行卡组与原始事实回读。",
                "fact_ids": relevant,
                "focus": ["来源覆盖", "运行资料"],
                "open_questions": [],
                "runtime_groups": runtime_groups,
                "chapter_ids": chapter_ids,
            })
        return {
            "cards": cards,
            "brief": runtime_brief,
            "chapter_briefs": chapter_briefs,
            "global_reference_fact_ids": global_references,
            "open_questions": [],
        }
    for index, chunk in enumerate(chunks, start=1):
        definition = definitions[(index - 1) % len(definitions)]
        display_name = definition.get("display_name") or definition["type"]
        cards.append(
            {
                "type": definition["type"],
                "title": f"FakeLLM {display_name} {index}",
                "purpose": "在离线流程中验证全局规划与原始事实回读。",
                "fact_ids": chunk,
                "focus": ["来源覆盖", "跨批次规划"],
                "open_questions": [],
                "chapter_ids": chapter_ids,
            }
        )
    return {
        "cards": cards,
        "brief": runtime_brief,
        "chapter_briefs": chapter_briefs,
        "global_reference_fact_ids": global_references,
        "open_questions": [],
    }


def _fake_materialize_output(messages: list[dict]) -> dict:
    content = "\n".join(str(message.get("content", "")) for message in messages)
    plan_match = re.search(r"^CARD_PLAN_JSON=(.+)$", content, re.MULTILINE)
    facts_match = re.search(r"^ORIGINAL_FACTS_JSON=(.+)$", content, re.MULTILINE)
    definition_match = re.search(r"^CARD_DEFINITION_JSON=(.+)$", content, re.MULTILINE)
    plan = json.loads(plan_match.group(1)) if plan_match else {}
    facts = json.loads(facts_match.group(1)) if facts_match else []
    definition = json.loads(definition_match.group(1)) if definition_match else {}
    available = {item["id"]: item for item in facts}
    fact_ids = [fact_id for fact_id in plan.get("fact_ids", []) if fact_id in available]
    source_text = " / ".join(available[fact_id]["text"] for fact_id in fact_ids[:3])[:700]
    list_fields = {
        "immediate_actions", "direct_clues", "hidden_clues", "gm_moves",
        "exit_conditions", "npc_hooks", "clock_links", "noncombat_exits",
        "stages", "usable_features", "hazards", "positioning",
        "environment_changes", "signature_actions", "resolutions",
        "entry_points", "discoveries", "relevant_characters",
        "major_conflicts", "opening_conditions", "possible_endings",
        "key_people", "factions", "cross_environment_consequences",
        "impulses", "gm_responses", "phase_changes", "exit_outcomes",
        "triggers", "potential_adversaries", "motives", "attitude_changes",
        "choices", "complications",
    }
    fields = {}
    field_sources = {}
    for field_name in definition.get("required_fields", []):
        fields[field_name] = [source_text] if field_name in list_fields else source_text
        field_sources[field_name] = list(fact_ids)
    card = {
        "type": plan.get("type", definition.get("type", "scene")),
        "title": plan.get("title", "FakeLLM 备团产物"),
        "subtitle": "离线分层生成验证草案",
        "fact_ids": fact_ids,
        "fields": fields,
        "field_sources": field_sources,
        "open_questions": [],
    }
    return {"cards": [card], "open_questions": []}


def _fake_structure_local_output(messages: list[dict]) -> dict:
    content = "\n".join(str(message.get("content", "")) for message in messages)
    match = re.search(r"^STRUCTURE_FACTS_JSON=(.+)$", content, re.MULTILINE)
    facts = json.loads(match.group(1)) if match else []
    return {
        "observations": [
            {
                "title": "离线叙事结构观察",
                "summary": " / ".join(str(item.get("text", "")) for item in facts[:3])[:900]
                or "离线结构验证",
                "fact_ids": [item["id"] for item in facts],
                "chapter_hints": ["开端", "发展"] if len(facts) > 6 else ["完整冒险"],
                "thread_hints": ["主要目标"],
                "beat_hints": ["局势出现变化"],
            }
        ]
    }


def _fake_structure_consolidate_output(messages: list[dict]) -> dict:
    content = "\n".join(str(message.get("content", "")) for message in messages)
    match = re.search(r"^STRUCTURE_OBSERVATIONS_JSON=(.+)$", content, re.MULTILINE)
    observations = json.loads(match.group(1)) if match else []
    fact_ids = list(
        dict.fromkeys(
            fact_id
            for observation in observations
            for fact_id in observation.get("fact_ids", [])
        )
    )
    chapter_count = 2 if len(fact_ids) > 6 else 1
    chapters = [
        {
            "key": f"phase_{index}",
            "title": "开端" if index == 1 else "发展与收束",
            "summary": "按叙事功能组织的离线冒险阶段。",
        }
        for index in range(1, chapter_count + 1)
    ]
    roles = {"phase_1": "introduce"}
    if chapter_count == 2:
        roles["phase_2"] = "resolve"
    return {
        "chapters": chapters,
        "threads": [
            {
                "key": "objective",
                "title": "目标争夺",
                "kind": "truth_chain",
                "summary": "贯穿冒险的离线验证剧情线。",
                "chapter_roles": roles,
            }
        ],
        "beats": [
            {
                "key": f"situation_changes_{chapter['key']}",
                "title": "局势发生变化",
                "summary": "这是可能发生的发展，不规定运行顺序。",
                "chapter_key": chapter["key"],
                "thread_keys": ["objective"],
                "thread_developments": {"objective": {
                    "development": "调查可能揭示各方对目标的不同诉求。",
                    "carry_forward": "保留尚未回应的诉求，供后续游戏继续处理。",
                }},
                "interplay": "队伍选择回应哪一方，会影响后续获得的信息；不预设解决路线。",
                "fact_ids": fact_ids[: min(3, len(fact_ids))],
            }
            for chapter in chapters
        ],
    }


def _fake_structure_assign_output(messages: list[dict]) -> dict:
    content = "\n".join(str(message.get("content", "")) for message in messages)
    facts_match = re.search(r"^STRUCTURE_FACTS_JSON=(.+)$", content, re.MULTILINE)
    skeleton_match = re.search(r"^STRUCTURE_SKELETON_JSON=(.+)$", content, re.MULTILINE)
    facts = json.loads(facts_match.group(1)) if facts_match else []
    skeleton = json.loads(skeleton_match.group(1)) if skeleton_match else {}
    chapter_keys = [item["key"] for item in skeleton.get("chapters", [])]
    thread_keys = [item["key"] for item in skeleton.get("threads", [])]
    assignments = []
    for index, fact in enumerate(facts):
        kind = "display_material" if fact.get("kind") == "handout" else "chapter"
        chapters = [] if kind == "display_material" else [chapter_keys[index % len(chapter_keys)]]
        assignments.append(
            {
                "fact_id": fact["id"],
                "kind": kind,
                "chapter_keys": chapters,
                "thread_keys": thread_keys[:1] if kind == "chapter" else [],
                "reason": "",
            }
        )
    return {"assignments": assignments}


class FakeLLM:
    """确定性的假 LLM：按 [TASK:...] 返回示例 JSON，用于无 key 验证。"""

    def __init__(self, cfg: dict):
        self.model = cfg.get("model", "fake")

    def _output(self, messages: list[dict]) -> str:
        task = find_task(messages)
        if task in {"prep:segment", "prep:segment:refine"}:
            return json.dumps(_fake_segment_output(messages), ensure_ascii=False, indent=2)
        if task == "prep:fact_extract":
            return json.dumps(_fake_prep_output(messages), ensure_ascii=False, indent=2)
        if task == "prep:consolidate":
            return json.dumps(
                _fake_consolidation_output(messages), ensure_ascii=False, indent=2
            )
        if task == "prep:artifact_draft":
            return json.dumps(_fake_artifact_output(messages), ensure_ascii=False, indent=2)
        if task == "prep:artifact_local_digest":
            return json.dumps(_fake_local_digest_output(messages), ensure_ascii=False, indent=2)
        if task == "prep:artifact_global_plan":
            return json.dumps(_fake_global_plan_output(messages), ensure_ascii=False, indent=2)
        if task == "prep:artifact_global_plan_leaf":
            return json.dumps(
                _fake_leaf_plan_output(messages), ensure_ascii=False, indent=2
            )
        if task == "prep:artifact_materialize":
            return json.dumps(_fake_materialize_output(messages), ensure_ascii=False, indent=2)
        if task == "prep:structure_local":
            return json.dumps(_fake_structure_local_output(messages), ensure_ascii=False, indent=2)
        if task == "prep:structure_consolidate":
            return json.dumps(
                _fake_structure_consolidate_output(messages), ensure_ascii=False, indent=2
            )
        if task == "prep:structure_assign":
            return json.dumps(_fake_structure_assign_output(messages), ensure_ascii=False, indent=2)
        data = FAKE_OUTPUTS.get(task)
        if data is None:
            data = {"note": f"假客户端未实现任务: {task!r}", "task": task}
        return json.dumps(data, ensure_ascii=False, indent=2)

    def chat(self, messages: list[dict], **kw) -> str:
        return self._output(messages)

    def chat_json(self, messages: list[dict], **kw) -> dict:
        return json.loads(self._output(messages))

    def stream(self, messages: list[dict], **kw) -> Iterator[str]:
        text = self._output(messages)
        for i in range(0, len(text), 24):
            yield text[i:i + 24]


def make_client(
    model_id: str | None = None, force_fake: bool | None = None
) -> LLMClient | FakeLLM:
    cfg = dict(storage.get_config())
    if model_id:
        cfg["model"] = model_id
    use_fake = cfg["fake"] if force_fake is None else force_fake
    return FakeLLM(cfg) if use_fake else LLMClient(cfg)
