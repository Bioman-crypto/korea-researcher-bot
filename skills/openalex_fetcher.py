import asyncio
import logging
import time
from datetime import date
from typing import Any, Callable, Coroutine, Optional

import aiohttp

import config

logger = logging.getLogger(__name__)

_OPENALEX_BASE = "https://api.openalex.org/works"
_HEADERS = {"User-Agent": f"KoreaResearcherBot ({config.CONTACT_EMAIL})"}

# Global semaphore: max 5 concurrent API requests across all users
_sem = asyncio.Semaphore(5)


def _from_date(since: Optional[int]) -> str:
    if since:
        return f"{since}-01-01"
    year = date.today().year - config.RESULTS_YEAR_RANGE
    return f"{year}-01-01"


_PAGE_COST_CREDITS = 10
_BUSY_RETRY_AFTER_MAX = 120  # Retry-After가 이보다 길면 "서버 부하"가 아니라 "한도 소진"으로 판단

# 키별 한도 소진 해제 시각 (index → epoch). 소진된 키는 리셋 전까지 건너뛴다.
_key_exhausted_until: dict[int, float] = {}

WaitCallback = Callable[[int], Coroutine]


class QuotaExhaustedError(Exception):
    """모든 API 키와 익명 접근의 하루 한도가 소진됨."""


def _int_header(headers, name: str) -> Optional[int]:
    try:
        return int(headers.get(name))
    except (TypeError, ValueError):
        return None


def _current_key() -> Optional[int]:
    """한도가 남은 첫 키의 index. 전부 소진이면 None(익명)."""
    now = time.time()
    for idx in range(len(config.OPENALEX_API_KEYS)):
        if _key_exhausted_until.get(idx, 0) <= now:
            return idx
    return None


def _mark_key_exhausted(idx: int, reset_seconds: Optional[int]) -> None:
    wait = reset_seconds if reset_seconds and reset_seconds > 0 else 3600
    _key_exhausted_until[idx] = time.time() + wait
    logger.warning(
        "OpenAlex API key #%d quota exhausted for %ds — switching to next key", idx + 1, wait
    )


async def _fetch_page(
    session: aiohttp.ClientSession,
    params: dict,
    wait_cb: Optional[WaitCallback] = None,
) -> dict:
    """
    한 페이지를 받을 때까지 시도한다.
    - 429 + 한도 소진: 다음 키로 즉시 전환. 키가 모두 소진이면 익명, 익명도 소진이면 QuotaExhaustedError.
    - 429 + 서버 부하: Retry-After만큼 대기 후 재시도 (횟수 제한 없음, 전체 기한은 호출자가 관리).
    - 네트워크/5xx: 지수 백오프로 MAX_NETWORK_RETRY회까지 재시도 후 raise.
    - 그 외 4xx: 즉시 raise (쿼리 자체 문제).
    """
    network_failures = 0
    while True:
        key_idx = _current_key()
        req_params = dict(params)
        if key_idx is not None:
            req_params["api_key"] = config.OPENALEX_API_KEYS[key_idx]
        key_label = f"key #{key_idx + 1}" if key_idx is not None else "anonymous"

        try:
            async with _sem:
                async with session.get(_OPENALEX_BASE, params=req_params, headers=_HEADERS) as resp:
                    remaining = _int_header(resp.headers, "X-RateLimit-Remaining")
                    reset = _int_header(resp.headers, "X-RateLimit-Reset")

                    if resp.status == 429:
                        retry_after = _int_header(resp.headers, "Retry-After")
                        quota_gone = (
                            (remaining is not None and remaining < _PAGE_COST_CREDITS)
                            or retry_after is None
                            or retry_after > _BUSY_RETRY_AFTER_MAX
                        )
                        if quota_gone:
                            if key_idx is None:
                                raise QuotaExhaustedError(
                                    f"All {len(config.OPENALEX_API_KEYS)} keys and anonymous quota exhausted"
                                )
                            _mark_key_exhausted(key_idx, reset or retry_after)
                            continue  # 다음 키로 즉시 재시도
                        logger.warning("OpenAlex busy (429, %s) — waiting %ds", key_label, retry_after)
                        if wait_cb:
                            await wait_cb(retry_after)
                        await asyncio.sleep(retry_after)
                        continue

                    if 400 <= resp.status < 500:
                        resp.raise_for_status()
                    if resp.status >= 500:
                        raise aiohttp.ClientResponseError(
                            resp.request_info, resp.history, status=resp.status, message=resp.reason or ""
                        )

                    data = await resp.json()
                    if key_idx is not None and remaining is not None and remaining < _PAGE_COST_CREDITS:
                        _mark_key_exhausted(key_idx, reset)  # 다음 페이지부터 다음 키 사용
                    await asyncio.sleep(config.API_REQUEST_DELAY)
                    return data

        except aiohttp.ClientResponseError as exc:
            if exc.status < 500:
                raise
            error: Exception = exc
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            error = exc

        network_failures += 1
        if network_failures >= config.MAX_NETWORK_RETRY:
            raise error
        wait = min(2 ** network_failures, 60)
        logger.warning("API error (attempt %d, %s): %s — retrying in %ds", network_failures, key_label, error, wait)
        await asyncio.sleep(wait)


async def fetch_works(
    query: str,
    since: Optional[int],
    progress_cb: Callable[[int, Optional[int]], Coroutine],
    wait_cb: Optional[WaitCallback] = None,
) -> list[dict]:
    """
    Fetch all matching works from OpenAlex with cursor pagination.

    progress_cb(collected, total_or_None) is awaited after each page.
    wait_cb(seconds) is awaited before each "server busy" wait.
    Raises QuotaExhaustedError when every key and anonymous access are out of quota.
    Returns list of raw work dicts.
    """
    base_filter = (
        f"title_and_abstract.search:{query},"
        f"institutions.country_code:KR,"
        f"type:article,"
        f"language:en,"
        f"from_publication_date:{_from_date(since)}"
    )
    params: dict[str, Any] = {
        "filter": base_filter,
        "per_page": 200,
        "cursor": "*",
        "select": "id,doi,title,publication_year,authorships",
    }

    works: list[dict] = []
    total: Optional[int] = None
    page_count = 0

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        while True:
            data = await _fetch_page(session, params, wait_cb)
            results = data.get("results", [])
            works.extend(results)
            page_count += 1

            if total is None:
                total = data.get("meta", {}).get("count")

            if page_count % 10 == 1:  # 첫 페이지 + 10페이지마다만 업데이트
                await progress_cb(len(works), total)

            next_cursor = data.get("meta", {}).get("next_cursor")
            if not next_cursor or not results or page_count >= config.MAX_FETCH_PAGES:
                break
            params["cursor"] = next_cursor

    return works


def extract_authors(
    works: list[dict],
    univ: Optional[str],
) -> list[dict]:
    """
    Extract and deduplicate corresponding authors from works.

    Deduplication key: OpenAlex author ID.
    Fallback: last author when is_corresponding is missing on all authorships.
    Returns list of {"name", "institution", "year"} sorted by name.
    """
    # author_id → {"name", "institution", "year"}
    seen: dict[str, dict] = {}

    for work in works:
        year: int = work.get("publication_year") or 0
        authorships: list[dict] = work.get("authorships") or []

        if not authorships:
            continue

        # Always use last author (Korean convention: last author = PI/corresponding)
        auth = authorships[-1]
        author = auth.get("author") or {}
        author_id: str = author.get("id") or ""
        name: str = author.get("display_name") or "Anonymous"
        institutions: list[dict] = auth.get("institutions") or []

        # Keep only Korean-affiliated last authors
        korean_insts = [i for i in institutions if i.get("country_code") == "KR"]
        if not korean_insts:
            continue

        institution: str = korean_insts[0].get("display_name") or "Affiliation unknown"

        key = author_id if author_id else f"__{name}__{institution}"

        existing = seen.get(key)
        if existing is None:
            seen[key] = {"name": name, "institution": institution, "year": year, "paper_count": 1}
        else:
            existing["paper_count"] += 1
            if year > existing["year"]:
                existing["institution"] = institution
                existing["year"] = year

    authors = list(seen.values())

    if univ:
        authors = [
            a for a in authors
            if univ.lower() in a["institution"].lower()
        ]

    # Sort by paper count descending
    authors.sort(key=lambda a: a["paper_count"], reverse=True)
    return authors
