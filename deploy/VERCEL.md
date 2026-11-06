# Vercel에 배포

Vercel에서는 파일을 저장할 수 없고 요청마다 다른 인스턴스가 처리할 수 있어 SQLite를 쓸 수 없습니다. 이 프로젝트는 Vercel에서 Neon(Postgres)을 씁니다. 로컬 개발과 Ubuntu 서버 배포는 그대로 SQLite를 씁니다.

## 1. 데이터베이스 만들기

1. Vercel 프로젝트의 **Storage** 탭에서 **Neon**을 선택해 새 데이터베이스를 만듭니다.
2. Region은 **AWS Asia Pacific (Singapore)** 를 고릅니다. `vercel.json`이 함수를 같은 지역(`sin1`)에서 실행하도록 지정해 두었습니다. 다른 지역을 고르면 `vercel.json`의 `regions`도 가까운 곳으로 바꿉니다.
3. 데이터베이스를 프로젝트에 연결합니다. 연결할 환경에 **Production**을 반드시 포함합니다.
4. 접두사(custom prefix)는 넣지 않습니다.
5. 프로젝트의 **Settings → Environment Variables**에 `DATABASE_URL`이 생겼는지 확인합니다. 이 값은 연결 풀을 거치는 주소이고, 앱은 이 주소를 씁니다.

테이블은 앱이 처음 시작할 때 자동으로 만들어집니다.

## 2. 환경 변수 넣기

**Settings → Environment Variables**에 Production 환경으로 넣습니다.

| 변수 | 값 | 필수 |
|---|---|---|
| `BASE_URL` | 서비스 주소. 예: `https://<프로젝트>-<팀>.vercel.app` | 권장 |
| `DATABASE_URL` | 1단계에서 자동으로 들어갑니다 | 필수 |
| `SECRET_KEY` | 아래 명령으로 만든 값 | 필수 |
| `CRON_SECRET` | 아래 명령으로 새로 만든 값. `SECRET_KEY`와 다르게 합니다 | 권장 |
| `LLM_API_KEY` | Anthropic API 키. 없으면 AI 코멘트 섹션만 나타나지 않습니다 | 선택 |
| `LLM_MODEL` | `claude-haiku-5-5` | 선택 |

```powershell
.\venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

- `BASE_URL`을 넣지 않으면 Vercel이 알려 주는 운영 주소를 씁니다.
- `CRON_SECRET`이 없으면 만료 데이터가 정리되지 않습니다.
- 이 값들을 저장소에 올리면 안 됩니다. 저장소가 공개되어 있습니다.

## 3. 배포

`main` 브랜치에 push하면 배포됩니다. 환경 변수를 바꾼 뒤에는 다시 배포해야 반영됩니다. **Deployments**에서 최근 배포의 **Redeploy**를 누르면 됩니다.

## 4. 확인

1. `<BASE_URL>/healthz`가 `{"status":"ok"}`를 돌려주는지 봅니다.
2. 게임 링크를 만들고, 다른 기기에서 공유 링크를 열어 15문항을 끝까지 진행합니다.
3. 결과 링크에서 레이더 차트와 추천 8개를 확인합니다.
4. **Settings → Cron Jobs**에 `/api/cron/cleanup`이 보이는지 확인하고, **Run**을 눌러 성공하는지 봅니다.
5. 브라우저로 `<BASE_URL>/api/cron/cleanup`을 열었을 때 401 오류가 나오는지 봅니다. 비밀 값 없이는 실행되지 않아야 합니다.

## 문제가 생기면

모든 주소에서 `FUNCTION_INVOCATION_FAILED`가 나오면 앱이 시작되지 못한 것입니다. 해당 배포의 **Logs**에서 아래 내용을 찾습니다.

| 로그에 나오는 내용 | 원인 | 조치 |
|---|---|---|
| `database_url` 또는 `secret_key`, `Field required` | 환경 변수가 없음 | 2단계를 확인하고 다시 배포 |
| `secret_key`, `string_too_short` | `SECRET_KEY`가 32자보다 짧음 | 새 값을 만들어 넣고 다시 배포 |
| `Vercel에서는 SQLite를 쓸 수 없습니다` | `DATABASE_URL`이 SQLite 주소 | Neon 주소로 교체 |
| `password authentication failed` | `DATABASE_URL`의 계정 정보가 틀림 | Storage 탭에서 값을 다시 확인 |
| `connection timeout expired` | DB에 닿지 않음 | 주소와 Neon 상태를 확인 |

## Ubuntu 서버 배포와 다른 점

- **결과 링크가 로그에 보입니다.** 서버 배포에서는 접속 로그를 꺼 두지만, Vercel 로그 화면에는 요청 주소가 표시됩니다. `/r/` 주소의 토큰도 포함되므로, 프로젝트에 다른 사람을 초대할 때 유의합니다.
- **페이지 조회 제한은 인스턴스별로 따로 셉니다.** 링크 생성, 게임 제출, AI 코멘트 제한은 DB로 세므로 인스턴스끼리 공유됩니다.
- **AI 코멘트가 드물게 두 번 호출될 수 있습니다.** 다른 인스턴스에 동시에 요청이 들어온 경우입니다. 저장은 한 번만 되고, 그 뒤로는 저장된 코멘트를 씁니다. 호출 실패 후 5분간 다시 호출하지 않는 규칙도 인스턴스별로 적용됩니다.
- **정리 작업은 Vercel Cron이 하루 한 번 실행합니다.** 예약은 한국 시간 새벽 4시이고, Hobby 요금제에서는 그 시간대 안에서 실행 시각이 달라질 수 있습니다. 실패해도 다시 시도하지 않으므로 다음 날 실행에서 함께 지워집니다. 만료된 링크는 정리 전에도 열리지 않습니다.

## Neon 무료 요금제

2026년 11월 기준입니다. 최신 내용은 [Neon 요금 안내](https://neon.com/pricing)에서 확인합니다.

- 저장 공간 1GB, 컴퓨트 100 CU-시간, 전송량 5GB입니다.
- 컴퓨트나 전송량을 다 쓰면 다음 결제 주기까지 DB가 멈추고, 저장 공간을 넘으면 쓰기가 막힙니다. 데이터가 지워지지는 않습니다.
- 5분 동안 요청이 없으면 DB가 쉬고, 다음 요청 때 다시 켜집니다. 이때 첫 응답이 조금 느릴 수 있습니다.
