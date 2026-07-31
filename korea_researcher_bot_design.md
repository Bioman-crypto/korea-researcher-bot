# 한국 연구자 검색 디스코드 봇 — 에이전트 시스템 설계서

> **목적**: 키워드를 입력하면 해당 분야를 연구하는 대한민국 교수(교신저자)의 이름과 소속을 반환하는 디스코드 봇 시스템 설계
> **작성 기준일**: 2026-05
> **최종 인터뷰 반영일**: 2026-05-19
> **구현 참조용 문서 (Claude Code에서 활용)**

---

## 1. 작업 컨텍스트 문서

### 1-1. 배경 및 목적

학술 협력 대상 또는 전문가 Pool을 빠르게 파악하기 위해, 특정 키워드 기반으로 최근 5년간 국내 SCI 논문의 교신저자를 자동 수집하는 도구가 필요하다. 무분별한 배포를 막기 위해 디스코드 역할(Role) 기반 접근 제어를 채택한다. 연구자 개인정보 보호를 위해 결과는 디스코드 채널 내 표시에 한정하며 파일 다운로드를 제공하지 않는다.

### 1-2. 시스템 범위

| 항목 | 내용 |
|------|------|
| 데이터 소스 | OpenAlex REST API (무료, 키 불필요) |
| 검색 범위 | 최근 5년 / 국가: South Korea (`KR`) / 유형: Article |
| 검색 대상 | 논문 제목(title) + 초록(abstract) |
| 결과 추출 | 교신저자(is_corresponding: true 또는 마지막 저자 폴백) 이름 + 소속기관 |
| 저자 동일인 판단 | OpenAlex author ID 기준 병합 |
| 접근 제어 | 디스코드 서버 내 지정 Role 보유자만 사용 가능 |
| 결과 출력 | 상위 50건 디스코드 임베드 표시 (파일 첨부 없음) |
| 캐싱 | 동일 쿼리 24시간 캐시 (재호출 없이 즉시 반환) |
| 언어 지원 | 영어 키워드만 지원 (v1) |

### 1-3. 입출력 정의

**입력**

```
/search [키워드] [옵션]
```

- 키워드 1개 또는 2개 이상
- 불리언 연산자 지원: `AND`, `OR`, `"정확한 문구"` (NOT는 v1 미지원)
- 옵션 필터:
  - `--univ [대학명]` : 특정 소속기관으로 결과 제한 (부분 문자열 매칭)
  - `--since [연도]` : 해당 연도 이후 논문만 포함 (기본값: 최근 5년)

**입력 예시**

```
/search quantum dot AND solar cell
/search CRISPR --univ KAIST
/search deep learning --since 2023
/search "large language model" --univ 서울대
```

**출력**

```
🔍 Query: quantum dot AND solar cell
📅 Period: 2021–2026 | 🇰🇷 Korea | Article
─────────────────────────────
23 corresponding authors found (showing top 50)

1. Hong Gildong — Seoul National University, Dept. of Chemistry
2. Kim Chulsoo — KAIST, Dept. of Materials Science
3. ...
─────────────────────────────
```

**한국어 키워드 입력 시 응답 (v1)**

```
⚠️ Korean keyword input is not supported in v1.
Please use English keywords (e.g., "quantum dot" instead of "양자점").
```

### 1-4. 제약조건

| 구분 | 내용 |
|------|------|
| 접근 제어 | 지정 Role 없으면 명령 차단, 오류 메시지 반환 |
| API 제한 | OpenAlex 무료 플랜: 초당 10회, 이메일 헤더 필수 |
| 페이지네이션 | 결과 최대 200건/페이지, 전체 수집 시 자동 반복 |
| 결과 표시 | 상위 50건 디스코드 임베드 (2000자 초과 시 여러 메시지로 분할) |
| 동시 요청 | 복수 사용자 동시 검색 허용, API 요청 간 딜레이로 레이트 리밋 준수 |
| 파일 출력 | CSV 등 파일 다운로드 제공 안 함 (개인정보 보호) |
| 언어 지원 | 영어 키워드만 (v1) — 한국어 감지 시 영어 안내 메시지 반환 |
| NOT 연산자 | v1 미지원 (로드맵에만 포함) |
| 운영 환경 | Python 3.10+, discord.py 2.x |

### 1-5. 용어 정의

| 용어 | 정의 |
|------|------|
| 교신저자 (Corresponding Author) | OpenAlex `authorships[].is_corresponding == true` 인 저자; 없으면 마지막 저자로 폴백 |
| 마지막 저자 폴백 | `is_corresponding` 필드가 없는 논문에서 저자 배열 마지막 항목을 교신저자로 간주 |
| Author ID 병합 | OpenAlex `author.id` 기준으로 동일인 판단 — 소속 표기나 이름 표기 차이 무관 |
| 대표 소속 | 병합된 저자의 가장 최근 논문에 기재된 소속기관 |
| Role | 디스코드 서버 내 관리자가 부여하는 권한 태그 |
| Operator Role | 봇 사용이 허용된 디스코드 Role 이름 (기본값: `researcher`) |
| Admin Role | 봇 관리 명령어를 쓸 수 있는 Role (기본값: `bot-admin`) |
| 캐시 | 동일 쿼리 결과를 24시간 보관해 재호출 없이 즉시 반환하는 인메모리 저장소 |

### 1-6. OpenAlex API 확보 방법

키가 필요 없는 무료 API이며, 아래 조건만 지키면 된다.

1. HTTP 요청 헤더에 `User-Agent: YourAppName (your@email.com)` 포함
2. 초당 10회 이하 요청 유지 (동시 검색 시 요청 간 딜레이로 준수)
3. 대량 수집 시 [OpenAlex Premium](https://openalex.org/pricing) 검토 (선택)

---

## 2. 워크플로우 정의

### 2-1. 전체 흐름도

```
사용자 /search 입력
        │
        ▼
[Step 1] 권한 검증 (코드)
  ├─ 권한 없음 → ❌ "You don't have permission..." 차단 메시지 반환 (종료)
  └─ 권한 있음 ↓
        │
        ▼
[Step 2] 키워드 파싱 및 언어 감지 (코드)
  ├─ 한국어 감지 → ⚠️ "Korean input not supported" 안내 메시지 (종료)
  ├─ NOT 연산자 포함 → ⚠️ "NOT operator not supported in v1" 안내 메시지 (종료)
  ├─ 불리언 연산자(AND/OR/"") 분해 + OpenAlex 쿼리 변환
  ├─ --univ / --since 옵션 파싱
  └─ 유효성 검증 (빈 키워드 등)
        │
        ▼
[Step 3] 캐시 확인 (코드)
  ├─ 동일 쿼리 + 24시간 이내 캐시 존재 → Step 5로 바로 이동 (API 호출 생략)
  └─ 캐시 없음 ↓
        │
        ▼
[Step 4] OpenAlex API 호출 (코드)
  ├─ "🔍 Searching... (page 1)" 진행률 메시지 전송
  ├─ 필터: publication_year ≥ 올해-5 (또는 --since 값), country=KR, type=article
  ├─ 페이지네이션 반복 (전체 수집) — 각 페이지마다 진행률 메시지 업데이트
  ├─ 요청 간 딜레이 적용 (레이트 리밋 준수)
  └─ 실패 시 → 자동 재시도 최대 3회 (지수 백오프)
        │
        ▼
[Step 5] 교신저자 추출 (코드)
  ├─ authorships[] 중 is_corresponding == true 필터
  ├─ is_corresponding 없는 논문 → 마지막 저자로 폴백
  ├─ OpenAlex author ID 기준 중복 제거 (동일인 병합)
  ├─ 대표 소속: 가장 최근 논문 소속기관 사용
  ├─ --univ 옵션 있으면 소속기관 부분 문자열 필터 적용
  └─ 결과를 캐시에 저장 (24h TTL)
        │
        ▼
[Step 6] 결과 포맷 및 전송 (코드)
  ├─ 상위 50건을 디스코드 임베드 메시지로 출력
  └─ 50건 초과 시 여러 임베드 메시지로 분할 전송
```

### 2-2. 관리자 워크플로우 (권한 관리)

권한 관리는 디스코드 서버 설정에서 직접 Role 부여/회수로 처리한다. 봇 명령어를 통한 권한 관리는 v1에서 제외한다.

**권한 부여 절차 (수동)**
1. 사용자가 관리자에게 DM으로 사용 요청
2. 관리자가 디스코드 서버 설정 → 멤버 → 해당 사용자 → `researcher` Role 추가

```
관리자 /listusers   → 현재 Operator Role(researcher) 보유자 목록 출력
```

### 2-3. 단계별 상세 정의

#### Step 1 — 권한 검증

| 항목 | 내용 |
|------|------|
| 처리 주체 | 코드 (결정론적) |
| 성공 기준 | 사용자가 Operator Role 또는 Admin Role 보유 |
| 검증 방법 | 규칙 기반 (Role 목록 포함 여부 체크) |
| 실패 처리 | 즉시 차단 + `"You don't have permission to use this command. Please contact the admin."` 반환 |

#### Step 2 — 키워드 파싱

| 항목 | 내용 |
|------|------|
| 처리 주체 | 코드 (결정론적) |
| 한국어 감지 | 유니코드 범위(U+AC00–U+D7A3) 포함 여부 체크 → 안내 메시지 반환 |
| NOT 감지 | `NOT` 연산자 포함 시 → `"NOT operator is not supported in v1."` 반환 |
| 처리 내용 | AND/OR/"" 연산자를 OpenAlex 쿼리 파라미터로 변환 |
| 옵션 파싱 | `--univ`, `--since` 플래그 분리 후 키워드에서 제거 |
| 실패 처리 | `"Invalid keyword format. Please try again."` 안내 메시지 |

**불리언 → OpenAlex 변환 규칙**

| 사용자 입력 | OpenAlex 파라미터 |
|-------------|------------------|
| `A AND B` | `filter=title_and_abstract.search:A,title_and_abstract.search:B` |
| `A OR B` | `filter=title_and_abstract.search:A\|B` |
| `"exact phrase"` | `filter=title_and_abstract.search:"exact phrase"` |
| `NOT C` | ⛔ v1 미지원 — 사용자에게 안내 후 종료 |

#### Step 3 — 캐시 확인

| 항목 | 내용 |
|------|------|
| 처리 주체 | 코드 (결정론적) |
| 캐시 키 | 정규화된 쿼리 문자열 + --univ + --since 옵션 포함 |
| TTL | 24시간 |
| 저장소 | 인메모리 딕셔너리 (서버 재시작 시 초기화) |
| 캐시 히트 | Step 6으로 바로 이동, "⚡ Cached result (24h)" 표시 |

#### Step 4 — OpenAlex API 호출

| 항목 | 내용 |
|------|------|
| 처리 주체 | 코드 (결정론적) |
| 엔드포인트 | `GET https://api.openalex.org/works` |
| 핵심 파라미터 | `filter=title_and_abstract.search:{query},from_publication_date:{date},institutions.country_code:KR,type:article` |
| 페이지네이션 | `per_page=200`, `cursor=*` 방식으로 전체 수집 |
| 진행률 업데이트 | 각 페이지 완료 시 `message.edit()` 으로 "🔍 Searching... (page N/M, X works collected)" 업데이트 |
| 동시 요청 처리 | asyncio 비동기로 여러 사용자 동시 처리, 요청 간 `asyncio.sleep(0.1)` 으로 레이트 리밋 준수 |
| 실패 처리 | 자동 재시도 최대 3회 (지수 백오프) → 3회 후 `"API error. Please try again later."` |

#### Step 5 — 교신저자 추출

| 항목 | 내용 |
|------|------|
| 처리 주체 | 코드 (결정론적) |
| 1차 추출 | `authorships[is_corresponding=true]` 필터 |
| 폴백 | `is_corresponding` 없는 논문 → 저자 배열 마지막 항목 사용 |
| 동일인 병합 | `author.id` (OpenAlex URI) 기준 Set으로 중복 제거 |
| 대표 소속 | 병합된 저자의 논문 중 가장 최근 `publication_year`의 소속기관 |
| --univ 필터 | 대표 소속에 --univ 값이 포함된 경우만 결과에 포함 (대소문자 무관) |
| 성공 기준 | 교신저자 1명 이상 추출됨 |
| 실패 처리 | `"No corresponding authors found for this query."` 안내 후 종료 |

**엣지 케이스**

| 상황 | 처리 방식 |
|------|----------|
| `is_corresponding` 없는 논문 | 마지막 저자 폴백 적용 |
| 소속기관 정보 없음 | "Affiliation unknown" 으로 표기 |
| 저자 이름 없음 | "Anonymous" 으로 표기 |
| author.id 없는 저자 | (display_name, 소속) 쌍으로 폴백 중복 제거 |
| 동일인이 여러 소속 보유 (이직 등) | 가장 최근 논문 소속만 대표 소속으로 표시 |

#### Step 6 — 결과 포맷 및 전송

| 항목 | 내용 |
|------|------|
| 처리 주체 | 코드 (결정론적) |
| 표시 건수 | 상위 50건 |
| 포맷 | 디스코드 embed 메시지 |
| 분할 기준 | embed 하나당 최대 25건, 50건이면 2개 embed |
| 캐시 히트 표시 | embed footer에 "⚡ Cached result (24h)" 표시 |
| 파일 첨부 | 없음 (개인정보 보호) |
| 성공 기준 | 메시지 전송 완료 확인 |
| 실패 처리 | 자동 재시도 1회 → 실패 시 `"Failed to send results. Please try again."` |

---

## 3. 구현 스펙

### 3-1. 폴더 구조

```
/korea-researcher-bot
├── CLAUDE.md                        # 메인 에이전트 지침 (오케스트레이터)
├── .env                             # 환경변수 (DISCORD_TOKEN, CONTACT_EMAIL 등)
├── /.claude
│   ├── /skills
│   │   ├── /openalex-fetcher        # OpenAlex API 호출 스킬
│   │   │   ├── SKILL.md
│   │   │   └── /scripts
│   │   │       ├── fetch_works.py   # API 호출 + 페이지네이션 + 진행률 업데이트
│   │   │       └── parse_authors.py # 교신저자 추출 + author ID 병합 + 폴백 처리
│   │   ├── /query-parser            # 키워드 파싱 스킬
│   │   │   ├── SKILL.md
│   │   │   └── /scripts
│   │   │       └── parse_query.py   # 불리언 → OpenAlex 쿼리 변환 + 한국어/NOT 감지
│   │   ├── /cache-manager           # 캐시 관리 스킬
│   │   │   ├── SKILL.md
│   │   │   └── /scripts
│   │   │       └── cache.py         # 인메모리 캐시 (24h TTL)
│   │   └── /result-formatter        # 결과 포맷 스킬
│   │       ├── SKILL.md
│   │       └── /scripts
│   │           └── format_output.py # 디스코드 embed 생성 (최대 50건, 분할 처리)
│   └── /agents
│       └── (단일 에이전트 구조 — 서브에이전트 없음)
├── bot.py                           # 봇 진입점 (명령어 라우팅)
├── config.py                        # 설정값 (Role 이름, 이메일, 연도 등)
└── requirements.txt
```

> `output/` 디렉터리는 파일 출력 기능을 제거하였으므로 불필요.

### 3-2. CLAUDE.md 핵심 섹션 목록

1. **봇 역할 및 목적** — 무엇을 하는 봇인지 한 문단 요약
2. **명령어 목록** — `/search`, `/listusers` 각 설명 (권한 관리 명령어는 디스코드 서버 설정으로 대체)
3. **권한 구조** — Operator Role / Admin Role 정의 및 명령어 매핑
4. **워크플로우 참조** — 각 Step별 어떤 스킬을 호출하는지
5. **에러 응답 템플릿** — 권한 없음, API 오류, 결과 없음, 한국어 감지, NOT 미지원 등 표준 메시지 (영어)
6. **운영 주의사항** — API rate limit, 개인정보 취급 주의, 파일 출력 금지 이유

### 3-3. 에이전트 구조

**단일 에이전트** (서브에이전트 없음)

워크플로우가 선형 6단계로 단순하고, 모든 처리가 코드 스크립트로 위임되므로 단일 봇 프로세스로 충분하다.

```
bot.py (오케스트레이터)
    ├── 명령어 수신 → 권한 검증
    ├── query-parser 스킬 호출 (한국어/NOT 감지 포함)
    ├── cache-manager 캐시 확인
    ├── openalex-fetcher 스킬 호출 (캐시 미스 시)
    └── result-formatter 스킬 호출 → 디스코드 전송
```

### 3-4. 스킬 목록 및 역할

| 스킬 이름 | 역할 | 트리거 조건 |
|-----------|------|------------|
| `query-parser` | 사용자 입력 키워드를 OpenAlex 필터 파라미터로 변환. 한국어/NOT 감지 및 --univ/--since 옵션 파싱 포함 | `/search` 명령어 수신 직후 |
| `cache-manager` | 동일 쿼리의 24h 캐시 확인 및 저장 | query-parser 성공 직후 |
| `openalex-fetcher` | OpenAlex API 호출, 페이지네이션, 교신저자 추출 (author ID 병합 + 마지막 저자 폴백) | 캐시 미스 시 |
| `result-formatter` | 추출 결과를 디스코드 embed (최대 50건, 분할 전송)으로 변환 | openalex-fetcher 또는 캐시 히트 직후 |

### 3-5. 주요 설정값 (config.py에 정의)

| 설정 키 | 기본값 | 설명 |
|---------|--------|------|
| `OPERATOR_ROLE` | `"researcher"` | 검색 사용 허용 Role 이름 |
| `ADMIN_ROLE` | `"bot-admin"` | 권한 관리 허용 Role 이름 |
| `CONTACT_EMAIL` | `"your@email.com"` | OpenAlex User-Agent 헤더용 |
| `RESULTS_YEAR_RANGE` | `5` | 최근 N년 필터 (--since 없을 때 기본값) |
| `INLINE_LIMIT` | `50` | 디스코드 인라인 표시 최대 건수 |
| `EMBED_PAGE_SIZE` | `25` | embed 하나당 최대 항목 수 (50건 → 2개 embed) |
| `MAX_RETRY` | `3` | API 실패 시 최대 재시도 횟수 |
| `CACHE_TTL_SECONDS` | `86400` | 캐시 유효 시간 (24시간 = 86400초) |
| `API_REQUEST_DELAY` | `0.1` | 동시 요청 시 API 호출 간 딜레이 (초) |

### 3-6. 주요 산출물 형식

**디스코드 Embed 구조 (캐시 미스, 최대 2개 메시지)**

```
[Embed 1 Title]  🔍 Researcher Search Results
[Description]   Query: quantum dot AND solar cell
                Period: 2021–2026 | 🇰🇷 Korea | Article
[Fields]        1. Hong Gildong — Seoul National University, Chemistry
                2. Kim Chulsoo — KAIST, Materials Science
                ...
                25. (25번째 항목)
[Footer]        23 authors found | Showing 1–25 of 50

[Embed 2 Title]  🔍 Researcher Search Results (continued)
[Fields]        26. ...
                ...
                50. (50번째 항목)
[Footer]        Showing 26–50 of 50
```

**캐시 히트 시 Footer**

```
[Footer]  ⚡ Cached result (24h) | 23 authors found | Showing 1–23
```

**영어 오류 메시지 템플릿**

| 상황 | 메시지 |
|------|--------|
| 권한 없음 | `"You don't have permission to use this command. Please contact the admin."` |
| 한국어 감지 | `"Korean keyword input is not supported in v1. Please use English keywords."` |
| NOT 연산자 | `"The NOT operator is not supported in v1."` |
| 빈 키워드 | `"Invalid keyword format. Please provide at least one keyword."` |
| API 오류 | `"API error after 3 retries. Please try again later."` |
| 결과 없음 | `"No corresponding authors found for this query."` |
| 전송 실패 | `"Failed to send results. Please try again."` |

---

## 4. 권한 관리 운영 가이드 (관리자용)

> 구현 완료 후 README에 포함할 내용

### 4-1. 권한 부여/회수 (디스코드 서버 설정으로 처리)

v1에서는 봇 명령어 대신 디스코드 서버 설정에서 직접 Role을 관리한다.

1. 사용자가 관리자에게 DM으로 사용 요청
2. 관리자: 디스코드 서버 → 멤버 탭 → 해당 사용자 클릭 → `researcher` Role 추가/제거

### 4-2. 봇 명령어 (관리자 전용)

| 명령어 | 효과 | 예시 |
|--------|------|------|
| `/listusers` | `researcher` Role 보유자 전체 목록 출력 | `/listusers` |

---

## 5. 배포 가이드 (비개발자용)

### 5-1. 권장 배포 환경

**Railway.app** (가장 단순한 옵션 — 무료 티어 포함)

1. [railway.app](https://railway.app) 가입
2. GitHub 저장소 연결
3. 환경변수 설정 (DISCORD_TOKEN, CONTACT_EMAIL)
4. 자동 배포 — 24/7 실행됨

### 5-2. 로컬 실행 (필요할 때만 켜는 경우)

```bash
# 최초 1회
pip install -r requirements.txt

# 매번 실행
python bot.py
```

### 5-3. 환경변수 (.env 파일)

```
DISCORD_TOKEN=여기에_봇_토큰
CONTACT_EMAIL=379khs@gmail.com
OPERATOR_ROLE=researcher
ADMIN_ROLE=bot-admin
```

---

## 6. 로드맵 (v2 이후 검토 항목)

| 항목 | 내용 | 우선순위 |
|------|------|---------|
| 랭킹 기능 | 해당 키워드 논문 수 기준 정렬 (/search keyword --sort) | 높음 |
| NOT 연산자 | 결과 수집 후 Python 레벨 필터링 구현 | 중간 |
| 한국어 지원 | KCI API 연동 (별도 데이터 파이프라인) | 중간 |
| 채널 제한 | 특정 채널에서만 봇 작동하도록 환경변수로 설정 | 낮음 |
| CSV 출력 | Admin Role 전용으로 선택적 활성화 (개인정보 정책 검토 후) | 낮음 |
| 캐시 퍼시스턴스 | 서버 재시작 후에도 캐시 유지 (Redis 또는 SQLite) | 낮음 |

---

## 7. 미결 항목 (구현 시 확정 필요)

| 항목 | 내용 | 권장 처리 |
|------|------|----------|
| 봇 토큰 보관 | Discord Bot Token 보안 저장 | `.env` 파일 + `python-dotenv` |
| 동시 검색 레이트 리밋 | 여러 사용자가 동시에 대용량 키워드 검색 시 API 초과 가능성 | `asyncio.Semaphore`로 동시 API 호출 수 제한 검토 |
| 캐시 메모리 한도 | 무한정 캐시 누적 방지 | 최대 N개 쿼리 LRU 캐시 또는 전체 메모리 제한 설정 |
| 한국어 감지 정확도 | 영어 텍스트 내 한국어 단어 혼용 처리 | 유니코드 범위 체크로 충분한지 테스트 필요 |
| --univ 부분 매칭 범위 | 'Seoul' 입력 시 '서울대', 'Seoul National Univ' 등 모두 매칭되는지 | 테스트 후 정규화 방식 결정 |
