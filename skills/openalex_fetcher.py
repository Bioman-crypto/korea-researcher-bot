import asyncio
import logging
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


async def _fetch_page(
    session: aiohttp.ClientSession,
    params: dict,
    attempt: int = 0,
) -> dict:
    try:
        async with _sem:
            async with session.get(
                _OPENALEX_BASE, params=params, headers=_HEADERS
            ) as resp:
                resp.raise_for_status()
                await asyncio.sleep(config.API_REQUEST_DELAY)
                return await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        if attempt >= config.MAX_RETRY - 1:
            raise
        wait = 2 ** attempt
        logger.warning("API error (attempt %d): %s — retrying in %ds", attempt + 1, exc, wait)
        await asyncio.sleep(wait)
        return await _fetch_page(session, params, attempt + 1)


async def fetch_works(
    query: str,
    since: Optional[int],
    progress_cb: Callable[[int, Optional[int]], Coroutine],
) -> list[dict]:
    """
    Fetch all matching works from OpenAlex with cursor pagination.

    progress_cb(collected, total_or_None) is awaited after each page.
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
            data = await _fetch_page(session, params)
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
