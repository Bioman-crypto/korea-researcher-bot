"""검색 로그를 Google Sheets에 기록. Railway는 파일시스템이 임시적이라
로컬 CSV 대신 영구 저장소인 Sheets를 사용한다.

대프컨 students_master 시트(읽기 전용)에서 discord_user_id로 student_id/industry_field를
조회해 검색 로그에 함께 남긴다."""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import config

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]
_HEADER = [
    "timestamp_utc", "user_id", "username", "guild_id", "guild_name",
    "keyword", "univ", "since", "student_id", "industry_field",
]

_client = None
_client_init_failed = False

_search_worksheet = None
_students_worksheet = None
_students_lookup_failed = False

_student_cache: Dict[str, Tuple[str, str]] = {}
_student_cache_at: float = 0.0


def _get_client():
    global _client, _client_init_failed
    if _client is not None or _client_init_failed:
        return _client

    if not config.GOOGLE_SERVICE_ACCOUNT_JSON:
        _client_init_failed = True
        return None

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        info = json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = Credentials.from_service_account_info(info, scopes=_SCOPES)
        _client = gspread.authorize(creds)
    except Exception as exc:
        logger.error("Failed to initialize Google Sheets client: %s", exc)
        _client_init_failed = True
        return None

    return _client


def _get_search_worksheet():
    global _search_worksheet

    if _search_worksheet is not None:
        return _search_worksheet

    if not config.GOOGLE_SHEETS_ID:
        logger.warning("GOOGLE_SHEETS_ID not set. Skipping search logging.")
        return None

    client = _get_client()
    if client is None:
        logger.warning("Google Sheets logging not configured (missing env vars). Skipping search logging.")
        return None

    try:
        import gspread

        sheet = client.open_by_key(config.GOOGLE_SHEETS_ID)
        try:
            ws = sheet.worksheet(config.GOOGLE_SHEETS_WORKSHEET)
        except gspread.WorksheetNotFound:
            ws = sheet.add_worksheet(title=config.GOOGLE_SHEETS_WORKSHEET, rows=1000, cols=len(_HEADER))
            ws.append_row(_HEADER)

        existing_header = ws.row_values(1)
        if existing_header != _HEADER:
            ws.update("A1", [_HEADER])

        _search_worksheet = ws
        logger.info("Connected to Google Sheets search log.")
    except Exception as exc:
        logger.error("Failed to initialize search log worksheet: %s", exc)
        return None

    return _search_worksheet


def _get_students_worksheet():
    global _students_worksheet, _students_lookup_failed

    if _students_worksheet is not None or _students_lookup_failed:
        return _students_worksheet

    if not config.STUDENTS_MASTER_SHEET_ID:
        _students_lookup_failed = True
        return None

    client = _get_client()
    if client is None:
        _students_lookup_failed = True
        return None

    try:
        sheet = client.open_by_key(config.STUDENTS_MASTER_SHEET_ID)
        _students_worksheet = sheet.worksheet(config.STUDENTS_MASTER_WORKSHEET)
    except Exception as exc:
        logger.error("Failed to open students_master worksheet: %s", exc)
        _students_lookup_failed = True
        return None

    return _students_worksheet


def _refresh_student_cache() -> None:
    global _student_cache, _student_cache_at

    ws = _get_students_worksheet()
    if ws is None:
        return

    try:
        records = ws.get_all_records()
        cache = {}
        for row in records:
            discord_id = str(row.get("discord_user_id") or "").strip()
            if not discord_id:
                continue
            cache[discord_id] = (
                str(row.get("student_id") or ""),
                str(row.get("industry_field") or ""),
            )
        _student_cache = cache
        _student_cache_at = time.monotonic()
    except Exception as exc:
        logger.error("Failed to refresh students_master cache: %s", exc)


def _lookup_student(user_id: int) -> Tuple[str, str]:
    if config.STUDENTS_MASTER_SHEET_ID and (
        time.monotonic() - _student_cache_at > config.STUDENTS_MASTER_CACHE_TTL_SECONDS
    ):
        _refresh_student_cache()

    return _student_cache.get(str(user_id), ("", ""))


def log_search_sync(
    user_id: int,
    username: str,
    guild_id: Optional[int],
    guild_name: Optional[str],
    keyword: str,
    univ: Optional[str],
    since: Optional[int],
) -> None:
    """Blocking call — run via asyncio.to_thread from the bot."""
    ws = _get_search_worksheet()
    if ws is None:
        return

    student_id, industry_field = _lookup_student(user_id)

    try:
        ws.append_row([
            datetime.now(timezone.utc).isoformat(),
            str(user_id),
            username,
            str(guild_id or ""),
            guild_name or "",
            keyword,
            univ or "",
            str(since or ""),
            student_id,
            industry_field,
        ])
    except Exception as exc:
        logger.error("Failed to append search log row: %s", exc)
