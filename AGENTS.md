# Korea Researcher Bot — Agent Instructions

## 봇 역할 및 목적

한국 연구자 검색 디스코드 봇. 사용자가 영어 키워드를 입력하면 OpenAlex API를 통해 최근 5년간 한국 기관 소속 교신저자(corresponding author)를 찾아 디스코드 임베드 메시지로 반환한다. 결과는 파일로 저장/전송하지 않는다.

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
| 키 9개 + 익명 한도 전부 소진 | `Today's search capacity has been used up. Please try again after 9:00 AM (KST).` |
| 13분 안에 못 끝남 (서버 부하 지속) | `The search server is very busy right now. Please try again in a few minutes.` |
| 서버 부하 대기 중 (진행 표시) | `The search server is busy — retrying automatically in ~Ns. Please wait.` |
| 결과 없음 | embed에 `No corresponding authors found for this query.` 표시 |

## 운영 주의사항

- OpenAlex API 무료 플랜: 초당 10회 제한. `_sem = asyncio.Semaphore(5)` + `API_REQUEST_DELAY`로 준수.
- **OpenAlex API 키 (2026-09-27 도입)**: 키 없이(익명) 호출하면 서버 부하 시 429로 막혀 검색이 중간에 실패함 (수강생 "CFD" 검색 200/1261에서 `API error after retries` 발생이 계기).
  - 한도: 키당 하루 10,000 크레딧, 익명 1,000 크레딧. 결과 1페이지(200건) = 10 크레딧 → CFD 검색 1회(7페이지) ≈ 70 크레딧. 리셋은 매일 약 09:00 KST.
  - 키 9개를 순서대로 사용: `OPENALEX_API_KEY`, `_SECONDARY`, `_TERTIARY`, `_QUATERNARY`, `_QUINARY`, `_SENARY`, `_SEPTENARY`, `_OCTONARY`, `_NONARY` (또는 쉼표 구분 `OPENALEX_API_KEYS`). 한 키의 한도가 소진되면 리셋 전까지 건너뛰고 다음 키로 전환 → 전부 소진 시 익명 → 익명도 소진 시 "09:00 이후 재시도" 안내. 리셋까지 기다렸다 DM으로 보내는 방식은 운영자 결정으로 **채택하지 않음**.
  - 서버 부하 429(Retry-After ≤ 120초)는 한도 소진이 아니므로 같은 키로 Retry-After만큼 기다렸다 재시도. 네트워크/5xx만 `MAX_NETWORK_RETRY`회 제한. 전체 기한 `SEARCH_DEADLINE_SECONDS`=13분(디스코드 응답 토큰 15분).
  - ⚠️ 여러 키 순환은 OpenAlex 정책상 키 차단 위험이 있음을 알고 운영자가 선택함. 키가 일괄 차단되면 이 부분부터 의심할 것.
- **배포 (Railway)**: GitHub `Bioman-crypto/korea-researcher-bot` main 푸시 → 자동 재배포. Railway 서비스 이름은 **`worker`** (Settings → Source Repo로 확인. 같은 계정의 `daepcon-chatbot`은 다른 봇이니 건드리지 말 것). **Variables를 바꾼 뒤에는 좌측 상단 보라색 `Deploy` 버튼을 눌러야 적용됨** — "N changes" 표시가 남아 있으면 아직 미적용.
- 결과는 디스코드에만 표시. CSV 저장/전송 기능 없음 (개인정보 보호).
- 캐시는 인메모리(24h TTL). 서버 재시작 시 초기화됨.
- 슬래시 커맨드는 `setup_hook`에서 글로벌 sync. 처음 배포 후 반영까지 최대 1시간 소요.

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
```
