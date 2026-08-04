# Korea Researcher Bot — Agent Instructions

## 봇 역할 및 목적

한국 연구자 검색 디스코드 봇. 사용자가 영어 키워드를 입력하면 OpenAlex API를 통해 최근 5년간 한국 기관 소속 교신저자(corresponding author)를 찾아 디스코드 임베드 메시지로 반환한다. 검색 결과(교신저자 목록)는 파일로 저장/전송하지 않는다. 단, 검색 로그(누가/언제/무슨 키워드)는 관리자 통계용으로 Google Sheets에 기록된다.

## 명령어

| 명령어 | 설명 | 권한 |
|--------|------|------|
| `/search keyword` | 키워드로 교신저자 검색. `univ`, `since` 옵션 지원 | Operator 또는 Admin Role |
| `/listusers` | `researcher` Role 보유자 목록 출력 | Admin Role만 |

## 권한 구조

- `researcher` Role → `/search` 사용 가능
- `bot-admin` Role → `/search` + `/listusers` 사용 가능
- 그 외 → 모든 명령어 차단

권한 부여/회수: 디스코드 서버 설정에서 직접 Role 관리 (봇 명령어 없음).

## 워크플로우

```
/search → 권한 검증 → query_parser → cache_manager
                                         ↓ 캐시 미스
                                    openalex_fetcher
                                         ↓
                                    extract_authors
                                         ↓
                                    result_formatter → 디스코드 전송
```

## 에러 응답 (영어)

| 상황 | 메시지 |
|------|--------|
| 권한 없음 | `You don't have permission to use this command. Please contact the admin.` |
| 한국어 감지 | `Korean keyword input is not supported in v1. Please use English keywords.` |
| NOT 연산자 | `The NOT operator is not supported in v1.` |
| 빈 키워드 | `Invalid keyword format. Please provide at least one keyword.` |
| API 오류 | `API error after retries. Please try again later.` |
| 결과 없음 | embed에 `No corresponding authors found for this query.` 표시 |

## 운영 주의사항

- OpenAlex API 무료 플랜: 초당 10회 제한. `_sem = asyncio.Semaphore(5)` + `API_REQUEST_DELAY`로 준수.
- 검색 결과(교신저자 목록)는 디스코드에만 표시. CSV 저장/전송 기능 없음 (개인정보 보호).
- 캐시는 인메모리(24h TTL). 서버 재시작 시 초기화됨.
- 슬래시 커맨드는 `setup_hook`에서 글로벌 sync. 처음 배포 후 반영까지 최대 1시간 소요.
- **검색 로그(sheets_logger.py)**: `/search` 호출마다 user_id/username/keyword/univ/since를 Google Sheets에 append. Railway 파일시스템은 임시적이라 로컬 CSV 대신 사용. `GOOGLE_SERVICE_ACCOUNT_JSON`(서비스 계정 JSON 내용 전체), `GOOGLE_SHEETS_ID`가 비어있으면 조용히 스킵됨(봇 동작에는 영향 없음). 서비스 계정은 grad-crm과 동일한 것을 재사용하되(`grad-crm-agent@daepcon-498505.iam.gserviceaccount.com`), 대상 시트는 검색 로그 전용 별도 시트를 사용해 대프컨 데이터와 분리.

## 파일 구조

```
bot.py                  # 진입점 + 슬래시 커맨드
config.py               # 설정값 (dotenv 로드)
.env                    # 비공개 환경변수 (토큰 등)
skills/
  query_parser.py       # 키워드 검증 및 정규화
  cache_manager.py      # 24h 인메모리 캐시
  openalex_fetcher.py   # API 호출 + 교신저자 추출
  result_formatter.py   # 디스코드 embed 생성
  sheets_logger.py      # 검색 로그 → Google Sheets 기록
```
