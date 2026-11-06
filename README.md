# 취향 밸런스

선물을 주려는 사람이 받을 사람에게 "이거 vs 저거" 밸런스 게임 링크를 보내면, 그 결과로 취향 프로필과 예산에 맞는 선물 후보를 받아 보는 모바일 우선 웹서비스입니다. 가입 없이 링크만으로 동작합니다.

- **조사자**: 별명, 관계, 상황, 예산을 입력하고 공유 링크(`/g/{slug}`)와 비밀 결과 링크(`/r/{admin_token}`)를 받습니다.
- **응답자**: 공유 링크에서 15문항 게임을 하고 유형 카드를 받습니다. 결과가 링크를 보낸 사람에게 전달된다는 사실은 결과 화면에 안내됩니다.
- **결과**: 취향 레이더, 키워드, 추천 선물 8개, 피해야 할 분야를 보여 줍니다.

## 로컬 실행

Python 3.12가 필요합니다. 프로젝트 루트에서 실행합니다.

**Windows (PowerShell)**

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
# 출력된 값을 .env의 SECRET_KEY에 넣는다
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**macOS / Linux**

```bash
python3.12 -m venv venv
venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"
# 출력된 값을 .env의 SECRET_KEY에 넣는다
venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

브라우저에서 <http://127.0.0.1:8000> 을 엽니다. 데이터베이스 파일과 테이블은 앱을 처음 실행할 때 자동으로 만들어집니다.

`SECRET_KEY`를 `.env.example`의 값 그대로 두면 앱이 시작되지 않습니다. 32자 이상의 값으로 바꿔야 합니다.

## 환경변수

`.env` 파일에 설정합니다. `BASE_URL`, `DATABASE_URL`, `SECRET_KEY`는 필수입니다.

| 변수 | 설명 | 예시 |
|---|---|---|
| `BASE_URL` | 서비스의 공개 주소입니다. 공유 링크, 결과 링크, OG 태그가 이 값으로 만들어집니다. `https://`로 시작하면 쿠키에 `Secure`가 붙습니다. | `http://127.0.0.1:8000` |
| `DATABASE_URL` | 데이터베이스 접속 문자열입니다. SQLite 상대 경로는 프로젝트 루트를 기준으로 합니다. Vercel에서는 Postgres 주소만 쓸 수 있습니다. | `sqlite:///./taste_balance.db` |
| `SECRET_KEY` | 쿠키와 CSRF 토큰 서명에 쓰는 비밀 키입니다. 32자 이상이어야 하고 저장소에 올리면 안 됩니다. | 위 명령으로 생성 |
| `LLM_API_KEY` | 선택. AI 선물 코멘트에 쓰는 Anthropic API 키입니다. 비워 두면 그 섹션만 나타나지 않습니다. | `sk-ant-...` |
| `LLM_MODEL` | 선택. 코멘트를 쓸 모델입니다. 값이 없으면 `claude-haiku-5-5`를 씁니다. | `claude-haiku-5-5` |
| `CRON_SECRET` | 선택. Vercel Cron이 만료 데이터를 정리할 때 쓰는 비밀 값입니다. 비워 두면 정리 경로가 열리지 않습니다. | 위 명령으로 생성 |

운영 중에 `SECRET_KEY`를 바꾸면 응답자가 자기 결과를 다시 볼 때 쓰는 쿠키가 무효가 됩니다. 저장된 설문과 결과 링크는 그대로 동작합니다.

## AI 선물 코멘트

결과 페이지에서 규칙 기반 추천 8개와 응답 요약을 LLM에 보내, 3개를 골라 이유를 2문장씩 받아 보여 줍니다.

- **페이지를 막지 않습니다.** 페이지를 먼저 보여 주고 코멘트는 별도 요청(`POST /r/{admin_token}/ai-comment`)으로 받습니다.
- **설문당 한 번만 호출합니다.** 결과는 `aicomment` 테이블에 저장하고, 동시에 들어온 요청은 먼저 온 호출의 결과를 함께 씁니다. Vercel에서 다른 인스턴스에 동시에 들어온 요청은 예외로, 드물게 두 번 호출될 수 있지만 저장은 한 번만 됩니다.
- **실패하면 이 섹션만 숨깁니다.** 키가 없거나 호출이 실패하거나 응답이 조건에 맞지 않을 때입니다. 실패한 설문은 5분 동안 다시 호출하지 않습니다.
- **보내는 내용**은 받는 사람 별명, 취향 유형과 키워드, 축별 성향, 일반 문항에서 고른 선택지, 후보 선물 정보입니다. 관계, 상황, 예산, 링크 토큰, 날짜, 재미 문항 답변은 보내지 않습니다. 프롬프트 내용은 로그에 남기지 않습니다.

## 폴더 구조

```text
taste-balance/
├── app/
│   ├── main.py            앱 생성, 보안 헤더, 오류 화면
│   ├── config.py          .env 설정 읽기
│   ├── db.py              DB 엔진과 세션
│   ├── models.py          Survey, Response, AiComment, RateLimitHit 모델
│   ├── security.py        링크 토큰, CSRF, 쿠키 서명, 조회 rate limit
│   ├── rate_limit.py      DB로 세는 rate limit (링크 생성, 제출, AI 코멘트)
│   ├── templating.py      Jinja2 설정과 필터
│   ├── routers/
│   │   ├── surveyor.py    조사자 흐름 (/, /new, /r/...)
│   │   ├── game.py        응답자 게임 (/g/...)
│   │   └── cron.py        Vercel Cron이 호출하는 정리 경로
│   ├── services/
│   │   ├── tags.py        취향 축, 카테고리, 한글 라벨
│   │   ├── questions.py   게임별 15문항 뽑기
│   │   ├── taste.py       취향 벡터와 축 위치 계산
│   │   ├── persona.py     유형 판정
│   │   ├── recommend.py   선물 추천과 결과 데이터 생성
│   │   ├── ai_comment.py  LLM 호출과 AI 코멘트 저장
│   │   └── cleanup.py     만료 데이터 삭제
│   ├── templates/         Jinja2 템플릿
│   └── static/            CSS, 바닐라 JS, OG 이미지
├── data/
│   ├── questions.json     문항 40개 (재미 문항 30%)
│   ├── gifts.json         선물 120개 (가격대별로 고르게)
│   └── personas.json      유형 8종
├── scripts/
│   ├── cleanup.py         만료된 설문과 응답 삭제
│   ├── preview_recommend.py   추천 결과를 터미널에서 미리 보기
│   └── simulate_recommend.py  무작위 응답으로 추천 쏠림 점검
├── deploy/                서버 설치 스크립트와 배포 안내 (DEPLOY.md, VERCEL.md)
├── tests/                 pytest 테스트
├── .env.example           환경변수 예시
├── vercel.json            Vercel 함수 지역과 정리 예약
├── .vercelignore          Vercel에 올리지 않을 파일
├── .python-version        Vercel에서 쓸 Python 버전
├── pytest.ini
└── requirements.txt
```

## 테스트

```powershell
.\venv\Scripts\python.exe -m pytest
```

테스트는 메모리 DB를 쓰므로 `.env`나 로컬 DB의 영향을 받지 않습니다. `questions.json`과 `gifts.json`의 형식과 추천 품질도 함께 검사하므로, 데이터를 고친 뒤에도 실행해 주세요.

Postgres에서 같은 테스트를 돌리려면 `TEST_DATABASE_URL`에 테스트 전용 DB 주소를 넣습니다. 테스트마다 테이블을 지우고 다시 만들므로 운영 DB 주소를 넣으면 안 됩니다.

추천 결과를 눈으로 보려면 아래 명령을 씁니다. 무작위 응답으로 결과를 만들고, 인자는 최소 예산, 최대 예산, 시드입니다.

```powershell
.\venv\Scripts\python.exe -m scripts.preview_recommend 30000 70000 7
```

## 추천 쏠림 점검

`questions.json`이나 `gifts.json`을 고친 뒤에는 추천이 특정 선물에 쏠리지 않는지 확인합니다. 무작위 응답 100개로 시뮬레이션하고, 기준을 넘으면 종료 코드 1을 돌려줍니다.

```powershell
.\venv\Scripts\python.exe scripts\simulate_recommend.py
.\venv\Scripts\python.exe scripts\simulate_recommend.py --games 1000 --seed 7
.\venv\Scripts\python.exe scripts\simulate_recommend.py --budget 30000 70000
```

"공정한 몫"은 판마다 후보 전원이 같은 확률로 뽑힌다고 할 때의 값입니다. 아래 중 하나라도 해당하면 쏠림으로 판정합니다.

| 항목 | 기준 |
|---|---|
| 선물 하나의 추천률 | 후보였던 판의 50%를 넘고 공정한 몫의 2배도 넘음 (후보였던 판이 20판 이상인 선물만) |
| 상위 10개 선물의 비중 | 공정한 몫의 2배를 넘음 |
| 카테고리 하나의 비중 | 전체 추천의 22%를 넘음 (카테고리당 2개 제한으로 상한은 25%) |
| 추천된 선물의 비율 | 후보였던 선물의 70%에 못 미침 |

실제 사람은 무작위로 답하지 않으므로, 이 점검은 데이터가 구조적으로 치우쳤는지를 보는 용도입니다.

## 데이터를 고칠 때

- **선물 태그**: 축 태그 가중치의 합은 선물마다 4로 맞춥니다. 태그가 많다는 이유로 점수가 오르는 일을 막기 위해서입니다.
- **가격**: `price_min`은 30만 원 이하여야 합니다. 예산 슬라이더의 상한이 30만 원이라 그보다 비싸면 추천될 수 없습니다.
- **주류**: 전통주를 제외한 술은 온라인으로 팔 수 없어 쇼핑 검색으로 살 수 없습니다. 위스키, 와인 같은 품목은 넣지 않습니다.
- **선택지 문구**: 근거 문장에 그대로 인용되므로 한글로 끝나는 짧은 명사구로 씁니다.

## 만료 데이터 정리

설문은 만든 지 30일이 지나면 만료됩니다. 만료된 링크는 곧바로 열리지 않고, 데이터는 정리 작업이 실행되어야 삭제됩니다. Vercel에서는 Vercel Cron이 매일 `/api/cron/cleanup`을 호출합니다. 서버에서는 아래처럼 `scripts/cleanup.py`를 실행합니다.

```bash
# 지우지 않고 삭제 대상 개수만 확인
venv/bin/python scripts/cleanup.py --dry-run

# 삭제
venv/bin/python scripts/cleanup.py
```

cron에는 절대 경로로 등록합니다. 아래는 매일 새벽 4시에 실행하는 예시입니다.

```cron
0 4 * * * /srv/taste-balance/venv/bin/python /srv/taste-balance/scripts/cleanup.py >> /var/log/taste-balance-cleanup.log 2>&1
```

## Vercel에 배포

Neon(Postgres)을 연결해 배포합니다. 절차와 서버 배포와의 차이는 [deploy/VERCEL.md](deploy/VERCEL.md)에 있습니다.

## 서버에 배포

Ubuntu 24.04 서버에 직접 설치할 수도 있습니다. 절차는 [deploy/DEPLOY.md](deploy/DEPLOY.md)에 있습니다. `deploy/setup.sh`가 systemd 서비스, Nginx, HTTPS 인증서, 정리 작업을 한 번에 설정합니다.

- 워커는 1개로 실행합니다. 페이지 조회 제한 기록이 프로세스 메모리에 있어, 워커가 여러 개면 그 제한이 워커별로 따로 적용됩니다.
- 접속 로그를 끕니다. `/r/` 주소에 비밀 토큰이 들어 있어 로그에 남으면 안 됩니다.
- Nginx가 접속자 주소를 `X-Forwarded-For`로 넘겨야 rate limit이 접속자별로 적용됩니다. Uvicorn은 같은 서버(`127.0.0.1`)의 프록시가 보낸 이 헤더만 신뢰합니다.
