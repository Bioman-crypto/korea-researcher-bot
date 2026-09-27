import os
from dotenv import load_dotenv

load_dotenv()

print(f"[DEBUG] All env keys: {sorted(os.environ.keys())}")

DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
CONTACT_EMAIL: str = os.getenv("CONTACT_EMAIL", "your@email.com")

# OpenAlex API 키 — 앞 키의 하루 한도가 소진되면 다음 키로 넘어간다.
# OPENALEX_API_KEYS(쉼표 구분) 또는 OPENALEX_API_KEY, OPENALEX_API_KEY_SECONDARY ... _NONARY 순서로 읽는다.
_OPENALEX_KEY_VARS = [
    "OPENALEX_API_KEY",
    "OPENALEX_API_KEY_SECONDARY",
    "OPENALEX_API_KEY_TERTIARY",
    "OPENALEX_API_KEY_QUATERNARY",
    "OPENALEX_API_KEY_QUINARY",
    "OPENALEX_API_KEY_SENARY",
    "OPENALEX_API_KEY_SEPTENARY",
    "OPENALEX_API_KEY_OCTONARY",
    "OPENALEX_API_KEY_NONARY",
]
_openalex_keys = os.getenv("OPENALEX_API_KEYS", "").split(",") + [os.getenv(v, "") for v in _OPENALEX_KEY_VARS]
OPENALEX_API_KEYS: list[str] = list(dict.fromkeys(k.strip() for k in _openalex_keys if k.strip()))
OPERATOR_ROLE: str = os.getenv("OPERATOR_ROLE", "researcher")
ADMIN_ROLE: str = os.getenv("ADMIN_ROLE", "bot-admin")

# 검색 로그용 Google Sheets (Railway 파일시스템은 임시적이라 CSV 대신 사용)
GOOGLE_SERVICE_ACCOUNT_JSON: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
GOOGLE_SHEETS_ID: str = os.getenv("GOOGLE_SHEETS_ID", "")
GOOGLE_SHEETS_WORKSHEET: str = os.getenv("GOOGLE_SHEETS_WORKSHEET", "search_log")

# 대프컨 students_master 시트 (읽기 전용) — 검색 로그에 student_id/industry_field를 덧붙이기 위함
STUDENTS_MASTER_SHEET_ID: str = os.getenv("STUDENTS_MASTER_SHEET_ID", "")
STUDENTS_MASTER_WORKSHEET: str = os.getenv("STUDENTS_MASTER_WORKSHEET", "students_master")
STUDENTS_MASTER_CACHE_TTL_SECONDS: int = 600

RESULTS_YEAR_RANGE: int = 3
INLINE_LIMIT: int = 200
EMBED_PAGE_SIZE: int = 40
MAX_FETCH_PAGES: int = 50
CACHE_TTL_SECONDS: int = 3600
API_REQUEST_DELAY: float = 0.1
# 429(서버 부하)는 Retry-After만큼 기다렸다 재시도(횟수 제한 없음, 아래 기한까지).
# 429가 아닌 네트워크/5xx 오류만 이 횟수까지 재시도.
MAX_NETWORK_RETRY: int = 6
# 검색 전체 기한 — 디스코드 응답 토큰 유효시간(15분) 안에 결과를 보내기 위함
SEARCH_DEADLINE_SECONDS: int = 13 * 60
