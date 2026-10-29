"""Claude 호출. 운영(Railway)은 API, 로컬 시험은 claude CLI(Pro 요금제)로 같은 프롬프트를 돌린다.

모델급은 웹 도구와 같다: 판정 = 검증한 Sonnet급, 경험 키워드 뽑기·배경지식·관심 분야 = Opus급.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import time

logger = logging.getLogger(__name__)

BACKEND = os.getenv("LADDER_BACKEND", "api")            # api | cli (cli는 로컬 시험용)
MODELS = {
    "judge": os.getenv("LADDER_JUDGE_MODEL", "claude-sonnet-5-5"),
    "deep": os.getenv("LADDER_DEEP_MODEL", "claude-opus-5-5"),
}
CLI_ALIAS = {"judge": "sonnet", "deep": "opus"}          # 검증(run_consistency.py 등)에 쓴 claude -p --model 값
MAX_TOKENS = 4096
CALL_TIMEOUT = 240                                       # 초. 디스코드 응답 기한(15분) 안에 재시도까지 끝나도록

_client = None


class LLMError(Exception):
    pass


def ready():
    if BACKEND == "cli":
        return bool(_claude_bin())
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _claude_bin():
    return shutil.which("claude") or shutil.which("claude.cmd")


def _api():
    global _client
    if _client is None:
        from anthropic import AsyncAnthropic
        _client = AsyncAnthropic(max_retries=3, timeout=CALL_TIMEOUT)
    return _client


async def _call_api(tier, prompt, system):
    r = await _api().messages.create(model=MODELS[tier], max_tokens=MAX_TOKENS, system=system,
                                     messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    return text, {"model": r.model, "in": r.usage.input_tokens, "out": r.usage.output_tokens}


async def _call_cli(tier, prompt, system):
    p = await asyncio.create_subprocess_exec(
        _claude_bin(), "-p", "--model", CLI_ALIAS[tier], "--tools", "", "--no-session-persistence",
        "--output-format", "json", "--system-prompt", system,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await asyncio.wait_for(p.communicate(prompt.encode("utf-8")), CALL_TIMEOUT)
    d = json.loads(out.decode("utf-8"))
    u = d.get("usage") or {}
    return d.get("result", ""), {"model": "cli:" + CLI_ALIAS[tier], "in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0)}


def parse_json(text):
    """모델 답에서 JSON 하나를 꺼낸다 (앞뒤 설명·```json 울타리가 붙어도)."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if not starts:
        raise ValueError("JSON 없음: " + t[:200])
    s = min(starts)
    e = t.rfind("}" if t[s] == "{" else "]")
    t = t[s:e + 1]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        # 모델이 가끔 남기는 형식 실수: 목록·객체 끝의 쉼표, 둥근 따옴표
        fixed = re.sub(r",\s*([}\]])", r"\1", t).replace("“", '"').replace("”", '"')
        return json.loads(fixed)


async def json_call(tier, prompt, system, check=None):
    """JSON을 돌려받는다. 형식이 깨지거나 check가 실패하면 한 번 더 부른다. → (obj, meta)"""
    t0 = time.monotonic()
    calls, last = [], None
    for attempt in range(3):
        try:
            text, meta = await (_call_cli if BACKEND == "cli" else _call_api)(tier, prompt, system)
            calls.append(meta)
            obj = parse_json(text)
            if check:
                check(obj)
            return obj, _sum(calls, t0)
        except Exception as exc:          # 형식 오류·API 오류 모두 한 번만 다시
            last = exc
            logger.warning("ladder %s call failed (attempt %d): %s", tier, attempt + 1, exc)
    raise LLMError(str(last)) from last


def _sum(calls, t0):
    return {"model": calls[-1]["model"], "in": sum(c["in"] for c in calls), "out": sum(c["out"] for c in calls),
            "calls": len(calls), "sec": round(time.monotonic() - t0, 1)}
