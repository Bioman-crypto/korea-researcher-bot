"""학생별 진행 상태 + 운영진용 기록.

Railway 파일시스템은 재배포 때 지워지므로 검색 로그와 같은 봇 전용 Google Sheets에 저장한다
(대프컨 운영 시트와 분리). 탭: ladder_state(학생당 1행), ladder_log(AI 호출 1건당 1행).
시트가 설정되지 않은 로컬 시험에서는 LADDER_LOCAL_STORE 파일(JSON)에 저장한다.
값은 RAW로 쓴다 — 학생 글이 '='로 시작해도 수식으로 읽히지 않게.
"""
import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import config
from skills import sheets_logger

logger = logging.getLogger(__name__)

STATE_TAB, LOG_TAB = "ladder_state", "ladder_log"
STATE_HEADER = ["discord_user_id", "student_id", "display_name", "thread_id", "updated_at_utc", "state_json"]
LOG_HEADER = ["timestamp_utc", "discord_user_id", "student_id", "display_name", "kind", "word", "kw", "question",
              "level", "uses_both", "my_link", "hint_copied_pct", "bg_copied_pct", "bg_terms_used", "same_pair_as_prev",
              "judge_model", "judge_in", "judge_out", "deep_model", "deep_in", "deep_out", "sec", "error", "detail_json"]
CELL_MAX = 45000                    # 시트 셀 한도 50,000자
LOCAL = os.getenv("LADDER_LOCAL_STORE", "")

_states: dict = {}                  # user_id(str) → state
_rows: dict = {}                    # user_id(str) → ladder_state 행 번호
_ws: dict = {}
_lock = threading.Lock()            # gspread 호출은 스레드에서 돌아서 순서를 지킨다


def _now():
    return datetime.now(timezone.utc).isoformat()


def _worksheet(tab, header):
    if tab in _ws:
        return _ws[tab]
    if not config.GOOGLE_SHEETS_ID:
        return None
    client = sheets_logger._get_client()
    if client is None:
        return None
    import gspread
    sheet = client.open_by_key(config.GOOGLE_SHEETS_ID)
    try:
        ws = sheet.worksheet(tab)
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(title=tab, rows=500, cols=len(header))
        ws.update("A1", [header])
    if ws.row_values(1) != header:
        ws.update("A1", [header])
    _ws[tab] = ws
    return ws


def using_sheets():
    return bool(config.GOOGLE_SHEETS_ID and config.GOOGLE_SERVICE_ACCOUNT_JSON)


# ---------- 상태 ----------
def _load_all_sync():
    with _lock:
        if not using_sheets():
            if LOCAL and Path(LOCAL).exists():
                _states.update(json.loads(Path(LOCAL).read_text(encoding="utf-8")))
            return len(_states)
        ws = _worksheet(STATE_TAB, STATE_HEADER)
        if ws is None:
            return 0
        for i, row in enumerate(ws.get_all_values()[1:], start=2):
            if len(row) >= 6 and row[0]:
                try:
                    _states[row[0]] = json.loads(row[5])
                    _rows[row[0]] = i
                except json.JSONDecodeError:
                    logger.error("ladder_state row %d: state_json 읽기 실패", i)
        return len(_states)


async def load_all():
    """시작할 때 한 번 전부 읽어 둔다 — 버튼을 누르면 3초 안에 입력창을 띄워야 해서 그때 시트를 읽을 시간이 없다."""
    n = await asyncio.to_thread(_load_all_sync)
    logger.info("ladder: %d명 상태 불러옴 (%s)", n, "Sheets" if using_sheets() else (LOCAL or "메모리만"))


def get(user_id):
    return _states.get(str(user_id))


def put(user_id, state):
    _states[str(user_id)] = state


def owner_of_thread(thread_id):
    for uid, s in _states.items():
        if s.get("thread_id") == thread_id:
            return uid
    return None


def _save_sync(uid, name, state):
    with _lock:
        if not using_sheets():
            if LOCAL:
                Path(LOCAL).write_text(json.dumps(_states, ensure_ascii=False, indent=1), encoding="utf-8")
            return
        ws = _worksheet(STATE_TAB, STATE_HEADER)
        if ws is None:
            return
        s = {**state, "hist": list(state["hist"])}             # 원본은 봇이 계속 쓰고 있어서 복사본으로 줄인다
        js = json.dumps(s, ensure_ascii=False)
        while len(js) > CELL_MAX and s["hist"]:                # 넘치면 오래된 질문부터 덜어 낸다
            s["hist"].pop(0)
            js = json.dumps(s, ensure_ascii=False)
        student_id, _ = sheets_logger._lookup_student(int(uid))
        row = [uid, student_id, name, str(state.get("thread_id") or ""), _now(), js]
        if uid in _rows:
            ws.update(f"A{_rows[uid]}:F{_rows[uid]}", [row], value_input_option="RAW")
        else:
            ws.append_row(row, value_input_option="RAW")
            cell = ws.find(uid, in_column=1)
            if cell:
                _rows[uid] = cell.row


async def save(user_id, name, state):
    put(user_id, state)
    try:
        await asyncio.to_thread(_save_sync, str(user_id), name, state)
    except Exception as exc:          # 저장 실패가 학생 화면을 멈추지 않게 (메모리에는 남아 있음)
        logger.error("ladder state save failed (%s): %s", user_id, exc)


# ---------- 기록 ----------
def _log_sync(row):
    with _lock:
        if not using_sheets():
            return
        row[2], _ = sheets_logger._lookup_student(int(row[1]))     # 시트를 읽을 수 있어서 스레드 안에서
        ws = _worksheet(LOG_TAB, LOG_HEADER)
        if ws is not None:
            ws.append_row(row, value_input_option="RAW")


def log(user_id, name, kind, *, word="", kw="", q="", h=None, same=None, judge=None, deep=None, error="", detail=None):
    """AI 호출 1건 = 1행. 배경 설명 (나)/(가) 판단 숫자와 토큰 수(비용)를 운영진이 시트에서 바로 본다."""
    uid = str(user_id)
    h = h or {}
    judge, deep = judge or {}, deep or {}
    d = json.dumps(detail, ensure_ascii=False) if detail is not None else ""
    row = [_now(), uid, "", name, kind, word, kw, q,
           str(h.get("level", "")), _ox(h.get("uses_both")), _ox(h.get("my_link")),
           str(h.get("copied", "")), str(h.get("bgCopied", "")), ", ".join(h.get("bgTerm") or []),
           "" if same is None else _ox(same),
           judge.get("model", ""), str(judge.get("in", "")), str(judge.get("out", "")),
           deep.get("model", ""), str(deep.get("in", "")), str(deep.get("out", "")),
           str(max(judge.get("sec", 0), deep.get("sec", 0)) or ""), error[:500], d[:CELL_MAX]]
    task = asyncio.create_task(asyncio.to_thread(_log_sync, row))
    task.add_done_callback(lambda t: not t.cancelled() and t.exception() and logger.error("ladder log failed: %s", t.exception()))


def _ox(v):
    return "" if v is None else ("O" if v else "X")
