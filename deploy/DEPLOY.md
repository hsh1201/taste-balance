# 서버에 배포하기

Ubuntu 24.04 서버 한 대에 설치합니다. Uvicorn이 앱을 돌리고, Nginx가 그 앞에서 HTTPS를 처리하며, cron이 만료 데이터를 정리합니다. 어느 업체의 서버든 Ubuntu 24.04면 절차는 같습니다.

**직접 해 주셔야 하는 일:** 서버와 도메인 만들기, 결제, 서버 로그인. 나머지는 설치 스크립트가 처리합니다.

## 1. 서버 만들기

- **운영체제:** Ubuntu 24.04 LTS
- **사양:** 메모리 1GB 이상
- **지역:** 사용자가 한국이면 서울
- **방화벽:** 22(SSH), 80(HTTP), 443(HTTPS) 포트를 엽니다.

예를 들어 AWS Lightsail은 인스턴스의 Networking 탭에서 **443 포트를 직접 추가**해야 합니다. 빠뜨리면 HTTPS 접속이 되지 않습니다. 고정 IP(Static IP)도 만들어 붙여 두세요. 서버를 다시 켜도 주소가 바뀌지 않습니다.

서버를 만들 때 받는 SSH 키 파일(`.pem`)은 잘 보관하고, 다른 사람과 공유하지 마세요.

## 2. 도메인 연결

도메인 관리 화면에서 A 레코드를 서버 IP로 지정합니다. 카카오톡 공유와 복사 버튼이 제대로 동작하려면 HTTPS가 필요하고, HTTPS 인증서에는 도메인이 필요합니다.

도메인이 아직 없다면 두 가지 방법이 있습니다.

- **DuckDNS:** 무료 서브도메인을 받을 수 있습니다. 다만 응답이 불안정하다는 보고가 있어 실제 서비스에는 구매한 도메인을 권합니다.
- **IP로 먼저 시험:** 4단계에서 `--no-tls`를 붙이면 HTTP로 설치합니다. 시험용이며, 나중에 도메인을 붙여 다시 실행하면 HTTPS로 바뀝니다.

## 3. 압축 파일 만들어 올리기

내 PC의 프로젝트 폴더에서 실행합니다. `서버IP`와 키 파일 경로는 바꿔 주세요.

```powershell
.\venv\Scripts\python.exe deploy\package.py
scp -i C:\경로\키.pem dist\taste-balance.tar.gz ubuntu@서버IP:~
```

`.env`, DB, 가상환경은 압축 파일에 들어가지 않습니다.

## 4. 서버에서 설치하기

```powershell
ssh -i C:\경로\키.pem ubuntu@서버IP
```

접속한 뒤 서버에서 실행합니다. 도메인과 이메일은 바꿔 주세요. 이메일은 인증서 만료 알림용이며 생략할 수 있습니다.

```bash
tar -xzf taste-balance.tar.gz
sudo bash taste-balance/deploy/setup.sh example.com you@example.com
```

중간에 `LLM_API_KEY`를 물어봅니다. 붙여 넣어도 화면에 보이지 않는 것이 정상입니다. AI 코멘트를 쓰지 않으려면 그냥 Enter를 누르세요.

도메인 없이 시험한다면:

```bash
sudo bash taste-balance/deploy/setup.sh 서버IP --no-tls
```

"설치가 끝났습니다"가 나오면 휴대폰으로 그 주소를 열어 봅니다.

## 5. 설치 후 꼭 할 일

**API 지출 한도를 설정하세요.** 이제 링크만 있으면 누구나 접속할 수 있어 AI 코멘트 호출이 늘어날 수 있습니다. IP별 제한은 걸려 있지만 전체 지출 상한은 아니므로, Anthropic 콘솔에서 한도를 정해 두세요.

그다음 조사 생성, 휴대폰으로 게임, 결과 확인, 카카오톡 미리보기까지 한 번 진행해 보세요.

## 업데이트

코드를 고친 뒤 3~4단계를 그대로 반복합니다. `.env`의 비밀 키와 DB는 그대로 유지되고, 재시작하는 몇 초 동안만 접속이 끊깁니다. 이전 압축 해제 폴더는 먼저 지워 주세요.

```bash
rm -rf taste-balance
tar -xzf taste-balance.tar.gz
sudo bash taste-balance/deploy/setup.sh example.com
```

## 운영 명령

| 할 일 | 명령 |
|---|---|
| 서비스 상태 | `systemctl status taste-balance` |
| 앱 로그 | `journalctl -u taste-balance -n 50` |
| 재시작 | `sudo systemctl restart taste-balance` |
| 설정 수정 | `sudo nano /opt/taste-balance/.env` 후 재시작 |
| 만료 데이터 정리 기록 | `journalctl -t taste-balance-cleanup` |
| 인증서 갱신 점검 | `sudo certbot renew --dry-run` |

인증서 갱신과 만료 데이터 정리(매일 새벽 4시)는 자동으로 돌아갑니다.

## 파일 위치

| 위치 | 내용 |
|---|---|
| `/opt/taste-balance` | 프로그램과 가상환경 |
| `/opt/taste-balance/.env` | 설정과 비밀 키 |
| `/var/lib/taste-balance/taste_balance.db` | 데이터베이스 |

DB는 이 파일 하나입니다. 잃으면 진행 중인 설문과 결과 링크가 모두 사라지니, 서버 업체의 스냅샷 기능을 켜 두길 권합니다. 내 PC의 개발 DB는 서버로 옮겨지지 않습니다.

## 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 인증서를 받지 못함 | 도메인 A 레코드가 서버 IP를 가리키는지, 80번 포트가 열려 있는지 |
| 주소가 아예 열리지 않음 | 서버 업체 방화벽의 80, 443 포트 |
| 502 오류 | `systemctl status taste-balance`와 앱 로그 |
| 공유 링크 주소가 틀림 | `/opt/taste-balance/.env`의 `BASE_URL` |
| AI 코멘트만 안 보임 | 앱 로그의 `AI 코멘트:` 줄, `LLM_API_KEY` 값 |
