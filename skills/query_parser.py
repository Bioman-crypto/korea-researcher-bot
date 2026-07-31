import re

_KOREAN_RE = re.compile(
    r'[가-힣ㄱ-ㅎㅏ-ㅣꥠ-꥿ힰ-퟿]'
)
_NOT_RE = re.compile(r'\bNOT\b')


def parse(keyword: str) -> dict:
    """
    Validate and normalize a search keyword.

    Returns a dict with:
      - "query": normalized search string for OpenAlex (None on error)
      - "error": English error message (None on success)
    """
    if _KOREAN_RE.search(keyword):
        return {
            "query": None,
            "error": (
                "Korean keyword input is not supported in v1. "
                "Please use English keywords."
            ),
        }

    if _NOT_RE.search(keyword):
        return {
            "query": None,
            "error": "The NOT operator is not supported in v1.",
        }

    if keyword.count('"') % 2 != 0:
        return {
            "query": None,
            "error": 'Unmatched quote detected. Close all quotes (e.g. "exact phrase" AND keyword).',
        }

    normalized = " ".join(keyword.split())
    if not normalized:
        return {
            "query": None,
            "error": "Invalid keyword format. Please provide at least one keyword.",
        }

    return {"query": normalized, "error": None}
