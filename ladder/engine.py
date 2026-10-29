"""주제 찾기 사다리 계산 — 웹 도구(관심찾기 에이전트/ask-smile-daepcon/tool.html)의 스크립트를 그대로 옮긴 것.

디스코드와 무관한 순수 함수만 둔다. 웹 도구를 고치면 여기도 같이 고친다.
"""
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import texts

PROMPTS = Path(__file__).resolve().parent / "prompts"
JUDGE_PROMPT = (PROMPTS / "judge_prompt.md").read_text(encoding="utf-8")
EXTRACT_PROMPT = (PROMPTS / "extract_prompt.md").read_text(encoding="utf-8")
SIGNAL_PROMPT = (PROMPTS / "signal_prompt.md").read_text(encoding="utf-8")
BG_PROMPT = (PROMPTS / "bg_prompt.md").read_text(encoding="utf-8")

# 검증 때 쓴 시스템 프롬프트 (run_consistency.py / persona_run.py)
JUDGE_SYSTEM = "너는 지시에 따라 JSON만 출력하는 판정기다. 도구를 쓰지 않는다."
DEEP_SYSTEM = ("너는 지시에 따라 JSON만 출력한다. 도구를 쓰지 않는다. "
               "문자열 값 안에서 무언가를 인용·강조할 때는 작은따옴표(')만 쓰고, 큰따옴표(\")는 JSON 문법에만 쓴다.")

NAMES = texts.LEVELS
HIST_KEEP = 30          # 웹 도구와 같이 최근 30개만 보관
KST = timezone(timedelta(hours=9))


def new_state(thread_id=None):
    return {"thread_id": thread_id, "answers": ["", "", "", "", ""], "kws": [], "words": [], "research": [],
            "suggest": [], "echo": [], "follow_up": "", "thin": False, "signal": None, "hist": [],
            "cur": {"word": "", "kw": ""}, "last_hint": "", "usage": {},
            "kw_asked": False,          # 처음 인터뷰 뒤 키워드를 물었나
            "fb": {}}                   # 피드백 메시지 id → 그 질문 (옛 메시지의 '고쳐 쓰기'도 맞는 질문을 열도록)


def remember_feedback(state, message_id, h):
    fb = state.setdefault("fb", {})
    fb[str(message_id)] = {"word": h["word"], "kw": h["kw"], "q": h["q"]}
    for k in list(fb)[:-HIST_KEEP]:
        del fb[k]


def today_kst():
    return datetime.now(KST).strftime("%Y-%m-%d")


def bump_usage(state, kind, limit):
    """하루 사용 횟수를 세고, 한도를 넘었으면 False (비용 안전장치)."""
    u = state.setdefault("usage", {})
    if u.get("date") != today_kst():
        u.clear()
        u["date"] = today_kst()
    if u.get(kind, 0) >= limit:
        return False
    u[kind] = u.get(kind, 0) + 1
    return True


def parse_kws(text):
    return list(dict.fromkeys(s.strip() for s in re.split(r"[,，\n]", text or "") if s.strip()))


def make_answers(answers):
    return "\n".join(f"인터뷰 {i + 1}: {(a or '').strip() or '(비워 둠)'}" for i, a in enumerate(answers))


def chosen_words(state):
    return [w["word"] for w in state["words"] if w.get("on")]


def pair_words(state):
    """조합 고르기에는 '문제' 단어만 (강점은 3단계 신호로만 쓴다). 문제가 없으면 고른 단어 전부."""
    p = [w["word"] for w in state["words"] if w.get("on") and w.get("kind") != "강점"]
    return p or chosen_words(state)


def random_pair(state):
    ws, ks = pair_words(state), state["kws"]
    if not ws or not ks:
        return None
    return random.choice(ws), random.choice(ks)


# ---------- 1단계: 경험 키워드 뽑기 ----------
def extract_prompt(state):
    return (EXTRACT_PROMPT.replace("{{ANSWERS}}", make_answers(state["answers"]))
            .replace("{{KEYWORDS}}", ", ".join(state["kws"]) or "(없음)"))


def apply_extract(state, r):
    if not isinstance(r, dict):
        raise ValueError("extract: JSON 객체가 아님")
    manual = [w for w in state["words"] if not w.get("from")]          # 직접 추가한 단어는 남긴다
    got = [{"word": str(w["word"]), "kind": "강점" if "강점" in str(w.get("kind") or "") else "문제",
            "from": _int(w.get("from")), "quote": str(w.get("quote") or ""), "on": True}
           for w in (r.get("words") or []) if isinstance(w, dict) and w.get("word")]
    state["words"] = got + [m for m in manual if not any(g["word"] == m["word"] for g in got)]
    state["echo"] = [str(e["text"]) for e in (r.get("echo") or []) if isinstance(e, dict) and e.get("text")]
    state["research"] = [str(t) for t in (r.get("research_terms") or []) if t]
    state["suggest"] = [{"for": str(g["for"]), "terms": [{"term": str(t["term"]), "what": str(t.get("what") or "")}
                                                         for t in g["terms"] if isinstance(t, dict) and t.get("term")]}
                        for g in (r.get("suggest") or []) if isinstance(g, dict) and g.get("for") and isinstance(g.get("terms"), list)]
    state["follow_up"] = str(r.get("follow_up") or "")
    state["thin"] = bool(r.get("thin"))


def suggest_options(state):
    """1단계 '이어 볼 만한 연구 키워드' 메뉴: 답에 섞여 있던 연구 키워드 + 제안 키워드. [(term, 설명)]"""
    out, seen = [], set()
    for t in state["research"]:
        if t not in seen:
            seen.add(t)
            out.append((t, texts.S1_RESEARCH_DESC))
    for g in state["suggest"]:
        for t in g["terms"]:
            if t["term"] not in seen:
                seen.add(t["term"])
                out.append((t["term"], texts.S1_SUGGEST_DESC.format(for_=g["for"], what=t["what"])))
    return out


def set_suggested(state, offered, selected):
    """메뉴에 보인 제안 키워드(offered) 중 고른 것(selected)만 내 키워드에 남긴다. 직접 쓴 키워드는 그대로."""
    keep = [k for k in state["kws"] if k not in offered or k in selected]
    state["kws"] = keep + [s for s in selected if s not in keep]


# ---------- 2단계: 판정 + 배경 ----------
def judge_prompt(word, kw, q):
    item = f"- id: Q01\n  내 경험 단어: {word}\n  연구 키워드: {kw}\n  학생 질문: {q}"
    return JUDGE_PROMPT.replace("{{ITEMS}}", item)


def bg_prompt(word, kw, q):
    return BG_PROMPT.replace("{{WORD}}", word).replace("{{KW}}", kw).replace("{{Q}}", q)


def to_level(v):
    """모델이 3, "3", "3단계", "따져보기" 어느 형태로 줘도 1~6으로 읽는다."""
    s = str(v if v is not None else "")
    for i in range(1, 7):
        if NAMES[i] in s:
            return i
    m = re.search(r"[1-6]", s)
    return int(m.group(0)) if m else 1


def _grams_ratio(src, dst):
    n = len(src) - 3
    return sum(src[i:i + 4] in dst for i in range(n)) / n if n > 0 else 0


def copied_ratio(q, hint):
    """고쳐 쓴 질문이 직전 힌트를 얼마나 옮겨 적었나: 힌트의 4글자 조각 중 질문에도 있는 비율."""
    h, t = re.sub(r"\s+", "", hint or ""), re.sub(r"\s+", "", q or "")
    return 0 if len(h) < 12 else _grams_ratio(h, t)


def from_bg_ratio(q, bg_text):
    """질문이 배경 문장을 얼마나 옮겨 적었나: 질문의 4글자 조각 중 배경에도 있는 비율."""
    t, b = re.sub(r"\s+", "", q or ""), re.sub(r"\s+", "", bg_text or "")
    return 0 if len(t) < 8 or not b else _grams_ratio(t, b)


def follow_target(answers, q):
    """AI 추가 질문이 가리키는 인터뷰 칸: 질문과 2글자 조각이 가장 많이 겹치는 답. 겹치는 게 없으면 가장 짧은 답 칸."""
    t = re.sub(r"\s+", "", q or "")
    grams = {t[i:i + 2] for i in range(len(t) - 1)}
    def score(a):
        a = re.sub(r"\s+", "", a or "")
        return sum(a[i:i + 2] in grams for i in range(len(a) - 1))
    filled = [i for i, a in enumerate(answers) if (a or "").strip()] or list(range(len(answers)))
    best = max(filled, key=lambda i: score(answers[i]))
    return best if score(answers[best]) else min(filled, key=lambda i: len(answers[i] or ""))


def apply_coach(state, word, kw, q, judged, bg):
    """판정 결과를 기록에 넣고 피드백 화면에 쓸 값을 돌려준다. bg는 실패하면 None."""
    j = judged[0] if isinstance(judged, list) and judged else judged
    if not isinstance(j, dict):
        raise ValueError("judge: JSON 객체가 아님")
    b = None
    if isinstance(bg, dict) and isinstance(bg.get("lines"), list):
        b = {"lines": [str(x) for x in bg["lines"] if x][:3], "terms": [str(x) for x in (bg.get("terms") or [])]}
        if not b["lines"]:
            b = None
    hist = state["hist"]
    last = hist[-1] if hist else None
    same = bool(last and last["word"] == word and last["kw"] == kw)
    copied = copied_ratio(q, state.get("last_hint")) if same and state.get("last_hint") else 0
    prev_bg = last.get("bg") if same else None          # 바로 앞 질문을 고쳐 쓴 경우만 ((나)→(가) 판단용)
    bg_copied = from_bg_ratio(q, " ".join(prev_bg["lines"])) if prev_bg else 0
    bg_term = [t for t in prev_bg["terms"] if t and t in q and t not in last["q"]] if prev_bg else []
    h = {"q": q, "level": to_level(j.get("level")), "word": word, "kw": kw,
         "reason": str(j.get("reason") or ""), "hint": str(j.get("hint") or ""), "next": str(j.get("next") or ""),
         "uses_both": bool(j.get("uses_both")), "my_link": bool(j.get("my_link")), "fact_note": str(j.get("fact_note") or ""),
         "copied": round(copied * 100), "bg": b, "bgCopied": round(bg_copied * 100), "bgTerm": bg_term}
    hist.append(h)
    del hist[:-HIST_KEEP]
    state["last_hint"] = h["hint"]
    return h, same


# ---------- 3단계: 관심 분야 ----------
def signal_hist(state):
    """관심 분야 입력에 넣을 질문: 바로 앞과 글자까지 똑같은 질문은 뺀다. (디스코드에는 예시 문장이 입력되지 않는다)"""
    out = []
    for h in state["hist"]:
        if out and out[-1]["word"] == h["word"] and out[-1]["kw"] == h["kw"] and out[-1]["q"].strip() == h["q"].strip():
            continue
        out.append(h)
    return out


def make_record(state):
    """persona_run.make_record / tool.html makeRecord 와 같은 형식."""
    sh = signal_hist(state)
    kinds = {w["word"]: w.get("kind") for w in state["words"]}
    words = list(dict.fromkeys(chosen_words(state) + [h["word"] for h in sh]))
    words = [f"{w}({kinds[w]})" if kinds.get(w) else w for w in words]
    lines = [make_answers(state["answers"]), "", "학생이 고른 내 경험 단어: " + (", ".join(words) or "(없음)"),
             "학생의 연구 키워드: " + (", ".join(state["kws"]) or "(없음)"), ""]
    prev = None
    for i, h in enumerate(sh):
        pair = (h["word"], h["kw"])
        tag = " (바로 앞 질문을 고쳐 씀)" if prev == pair else ""
        lines.append(f"질문 {i + 1}{tag}: ({h['word']} × {h['kw']}) {h['q']} → 판정 {NAMES.get(h['level'], h['level'])}, 직전 힌트 옮겨 적음 {h.get('copied') or 0}%")
        prev = pair
    return "\n".join(lines)


def signal_prompt(state):
    return SIGNAL_PROMPT.replace("{{RECORD}}", make_record(state))


def apply_signal(state, r):
    if not isinstance(r, dict) or not isinstance(r.get("candidates"), list):
        raise ValueError("signal: candidates 없음")
    state["signal"] = r


def search_cmd(terms):
    """/search 의 keyword 칸에 넣을 글. 디스코드는 명령 전체를 붙여 넣으면 인식하지 못해서 keyword 부분만 준다."""
    t = [re.sub(r'["“”]', "", str(s)).strip() for s in (terms if isinstance(terms, list) else [])]
    t = [s for s in t if s]
    if not t:
        return "", []
    return f'"{t[0]}"' + (f' AND "{t[1]}"' if len(t) > 1 else ""), t[2:]


# ---------- 기록 ----------
def bg_check(state):
    """배경 설명((나))을 계속 쓸지 판단하는 숫자. 운영진용 — 학생 기록 파일에는 넣지 않는다."""
    hist = state["hist"]
    shown = rewrites = up = term = copied = link_off = moved_on = has_next = 0
    for i, h in enumerate(hist):
        if h.get("bg"):
            shown += 1
        prev = hist[i - 1] if i else None
        nxt = hist[i + 1] if i + 1 < len(hist) else None
        if h.get("bg") and nxt:
            has_next += 1
            if nxt["word"] != h["word"] or nxt["kw"] != h["kw"]:
                moved_on += 1
        if not prev or not prev.get("bg") or prev["word"] != h["word"] or prev["kw"] != h["kw"]:
            continue
        rewrites += 1
        up += h["level"] > prev["level"]
        term += bool(h.get("bgTerm"))
        copied += (h.get("bgCopied") or 0) >= 40
        link_off += not h.get("my_link")
    if not shown:
        return []
    return [f"배경을 보여 준 질문 {shown}개",
            f"배경을 본 뒤 같은 짝으로 고쳐 쓴 질문 {rewrites}개: 칸이 오름 {up} · 배경 개념을 새로 씀 {term} · 배경 문장 40% 이상 옮겨 적음 {copied} · 내 경험 연결 × {link_off}",
            f"배경을 본 뒤 고쳐 쓰지 않고 다른 짝으로 넘어감 {moved_on} (다음 질문이 있는 {has_next}개 중)"]


def record_text(state, for_admin=False):
    """학생에게 주는 기록 파일. for_admin이면 배경 설명 점검 숫자까지 (웹 도구 '기록 복사'와 같은 내용)."""
    p = ["[주제 찾기 사다리 기록]", "", "## 인터뷰"]
    p += [f"{i + 1}. {q}\n   → {(a or '').strip() or '(비워 둠)'}" for i, ((q, _, _), a) in enumerate(zip(texts.INTERVIEW, state["answers"]))]
    p += ["경험 키워드: " + (", ".join(f"{w['word']}({'잘하는 것' if w.get('kind') == '강점' else '궁금증·불편'})" for w in state["words"] if w.get("on")) or "(없음)"),
          "연구 키워드: " + (", ".join(state["kws"]) or "(없음)"), "", "## 질문"]
    for i, h in enumerate(state["hist"]):
        s = f"{i + 1}. ({h['word']} × {h['kw']}) {h['q']}\n   단계: {NAMES.get(h['level'], h['level'])}"
        if h.get("reason"):
            s += f"\n   이유: {texts.plain(h['reason'])}"
            if h.get("bg"):
                s += "\n   배경지식: " + " / ".join(map(texts.plain, h["bg"]["lines"]))
            s += f"\n   다음 단계로 가려면: {texts.plain(h['hint'])}\n   다음에 생각해 볼 질문: {texts.plain(h['next'])}"
            s += f"\n   두 키워드 {'○' if h.get('uses_both') else '×'} · 내 경험 연결 {'○' if h.get('my_link') else '×'}"
            if for_admin:
                s += (f" · 직전 힌트 옮겨 적음 {h['copied']}%" if h.get("copied") else "") + \
                     (f" · 직전 배경 옮겨 적음 {h['bgCopied']}%" if h.get("bgCopied") else "") + \
                     (f" · 배경 개념 새로 씀: {', '.join(h['bgTerm'])}" if h.get("bgTerm") else "")
        p.append(s)
    if for_admin:
        bl = bg_check(state)
        if bl:
            p += ["", "## 배경 설명 점검", *bl]
    s = state.get("signal")
    if s:
        p += ["", "## 내 관심이 모이는 곳"]
        P = texts.plain
        for c in s.get("candidates") or []:
            cmd, _ = search_cmd(c.get("search_terms"))
            p.append(f"- {P(c.get('title'))}\n  이유: {P(c.get('why'))}\n  내가 쓴 말: {' / '.join(map(P, c.get('evidence') or []))}\n  이렇게 이어 가 보세요: {P(c.get('explore'))}\n  연구실 탐색기 검색어(/search keyword 칸): {cmd}")
        p += ["1강 숙제 '문제' 칸 초안: " + P(s.get("problem_draft")),
              "내 연구 키워드 후보: " + ", ".join(map(str, s.get("keywords") or [])),
              "다음에 해 볼 것: " + P(s.get("next_step"))]
    return "\n".join(p)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0
