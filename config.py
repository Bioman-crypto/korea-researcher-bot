import os
from dotenv import load_dotenv

load_dotenv()

print(f"[DEBUG] All env keys: {sorted(os.environ.keys())}")

DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
CONTACT_EMAIL: str = os.getenv("CONTACT_EMAIL", "your@email.com")
OPERATOR_ROLE: str = os.getenv("OPERATOR_ROLE", "researcher")
ADMIN_ROLE: str = os.getenv("ADMIN_ROLE", "bot-admin")

# 검색 로그용 Google Sheets (Railway 파일시스템은 임시적이라 CSV 대신 사용)
GOOGLE_SERVICE_ACCOUNT_JSON: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
GOOGLE_SHEETS_ID: str = os.getenv("GOOGLE_SHEETS_ID", "")
GOOGLE_SHEETS_WORKSHEET: str = os.getenv("GOOGLE_SHEETS_WORKSHEET", "search_log")

RESULTS_YEAR_RANGE: int = 3
INLINE_LIMIT: int = 200
EMBED_PAGE_SIZE: int = 40
MAX_RETRY: int = 3
MAX_FETCH_PAGES: int = 50
CACHE_TTL_SECONDS: int = 3600
API_REQUEST_DELAY: float = 0.1
