import time
from typing import Any, Optional

import config

_store: dict[str, tuple[Any, float]] = {}


def make_key(query: str, univ: Optional[str], since: Optional[int]) -> str:
    return f"{query.lower()}|{(univ or '').lower()}|{since or 0}"


def get(key: str) -> Optional[Any]:
    entry = _store.get(key)
    if entry is None:
        return None
    value, ts = entry
    if time.time() - ts > config.CACHE_TTL_SECONDS:
        del _store[key]
        return None
    return value


def set(key: str, value: Any) -> None:
    _store[key] = (value, time.time())
