"""검색 로그를 Google Sheets에 기록. Railway는 파일시스템이 임시적이라
로컬 CSV 대신 영구 저장소인 Sheets를 사용한다."""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import config

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_HEADER = ["timestamp_utc", "user_id", "username", "guild_id", "guild_name", "keyword", "univ", "since"]

_worksheet = None
_init_failed = False


def _get_worksheet():
    global _worksheet, _init_failed
    if _worksheet is not None or _init_failed:
        return _worksheet

    if not config.GOOGLE_SERVICE_ACCOUNT_JSON or not config.GOOGLE_SHEETS_ID:
        logger.warning("Google Sheets logging not configured (missing env vars). Skipping search logging.")
        _init_failed = True
        return None

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        info = json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = Credentials.from_service_account_info(info, scopes=_SCOPES)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(config.GOOGLE_SHEETS_ID)

        try:
            ws = sheet.worksheet(config.GOOGLE_SHEETS_WORKSHEET)
        except gspread.WorksheetNotFound:
            ws = sheet.add_worksheet(title=config.GOOGLE_SHEETS_WORKSHEET, rows=1000, cols=len(_HEADER))
            ws.append_row(_HEADER)

        _worksheet = ws
        logger.info("Connected to Google Sheets search log.")
    except Exception as exc:
        logger.error("Failed to initialize Google Sheets logging: %s", exc)
        _init_failed = True
        return None

    return _worksheet


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
    ws = _get_worksheet()
    if ws is None:
        return
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
        ])
    except Exception as exc:
        logger.error("Failed to append search log row: %s", exc)
