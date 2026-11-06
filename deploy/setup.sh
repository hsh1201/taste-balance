#!/usr/bin/env bash
# Ubuntu 24.04 서버에 취향 밸런스를 설치하거나 업데이트한다. 여러 번 실행해도 안전하다.
#
#   sudo bash deploy/setup.sh example.com you@example.com   # HTTPS로 설치
#   sudo bash deploy/setup.sh 203.0.113.10 --no-tls         # 도메인 없이 HTTP로 시험
#
# 다시 실행해도 .env의 비밀 키와 DB는 그대로 두고 코드만 바꾼다.
set -euo pipefail
umask 022

APP_DIR=/opt/taste-balance
DATA_DIR=/var/lib/taste-balance
APP_USER=tastebalance
WEBROOT=/var/www/certbot
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() {
  echo "오류: $*" >&2
  exit 1
}

usage() {
  cat >&2 <<'EOF'
사용법: sudo bash deploy/setup.sh <도메인 또는 IP> [인증서 알림 이메일] [--no-tls] [--public-url URL]
  --no-tls          HTTPS 인증서 없이 HTTP로 설치합니다. 시험용입니다.
  --public-url URL  공유 링크에 쓸 주소를 직접 지정합니다. 보통은 필요 없습니다.
EOF
  exit 2
}

ADDRESS=""
EMAIL=""
USE_TLS=1
PUBLIC_URL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-tls)
      USE_TLS=0
      shift
      ;;
    --public-url)
      [[ $# -ge 2 ]] || usage
      PUBLIC_URL="$2"
      shift 2
      ;;
    -*)
      usage
      ;;
    *)
      if [[ -z "$ADDRESS" ]]; then
        ADDRESS="$1"
      elif [[ -z "$EMAIL" ]]; then
        EMAIL="$1"
      else
        usage
      fi
      shift
      ;;
  esac
done

[[ -n "$ADDRESS" ]] || usage
[[ $EUID -eq 0 ]] || die "sudo로 실행해 주세요."
# 설정 파일에 그대로 들어가는 값이므로 형식을 먼저 확인한다.
[[ "$ADDRESS" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || die "주소 형식이 올바르지 않습니다: $ADDRESS"
[[ -z "$EMAIL" || "$EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+$ ]] || die "이메일 형식이 올바르지 않습니다."
[[ -z "$PUBLIC_URL" || "$PUBLIC_URL" =~ ^https?://[A-Za-z0-9.:-]+$ ]] || die "--public-url 형식이 올바르지 않습니다."
[[ -f "$SOURCE_DIR/app/main.py" ]] || die "프로그램 파일을 찾지 못했습니다. 압축을 푼 폴더 안의 deploy/setup.sh를 실행해 주세요."
[[ "$SOURCE_DIR" != "$APP_DIR" ]] || die "설치 폴더가 아니라 압축을 푼 폴더에서 실행해 주세요."

if [[ -n "$PUBLIC_URL" ]]; then
  BASE_URL="$PUBLIC_URL"
elif [[ $USE_TLS -eq 1 ]]; then
  BASE_URL="https://$ADDRESS"
else
  BASE_URL="http://$ADDRESS"
fi

echo "==> 패키지 설치"
export DEBIAN_FRONTEND=noninteractive
packages=(python3 python3-venv nginx curl cron)
[[ $USE_TLS -eq 0 ]] || packages+=(certbot)
apt-get update -qq
apt-get install -y -qq "${packages[@]}" >/dev/null
python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' || die "Python 3.12 이상이 필요합니다. Ubuntu 24.04를 사용해 주세요."

echo "==> 서비스 계정과 폴더 준비"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR" "$DATA_DIR" "$WEBROOT"
chown "$APP_USER:$APP_USER" "$DATA_DIR"
chmod 750 "$DATA_DIR"

echo "==> 프로그램 복사"
systemctl stop taste-balance 2>/dev/null || true
# 코드는 root 소유로 두어 서비스가 뚫려도 고칠 수 없게 한다. 쓰기는 DATA_DIR에서만 한다.
for item in app data scripts; do
  rm -rf "${APP_DIR:?}/$item"
  cp -R "$SOURCE_DIR/$item" "$APP_DIR/$item"
done
cp "$SOURCE_DIR/requirements.txt" "$APP_DIR/requirements.txt"
chown -R root:root "$APP_DIR/app" "$APP_DIR/data" "$APP_DIR/scripts" "$APP_DIR/requirements.txt"

echo "==> 파이썬 의존성 설치"
[[ -x "$APP_DIR/venv/bin/python" ]] || python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$APP_DIR/requirements.txt"

ENV_FILE="$APP_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
  echo "==> 기존 .env 유지 (BASE_URL만 갱신)"
  sed -i "s|^BASE_URL=.*|BASE_URL=${BASE_URL}|" "$ENV_FILE"
else
  echo "==> .env 생성"
  SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  LLM_KEY="${LLM_API_KEY:-}"
  if [[ -z "$LLM_KEY" && -t 0 ]]; then
    # 명령줄 인자로 받으면 셸 기록과 프로세스 목록에 남으므로 화면에 보이지 않게 입력받는다.
    read -rsp "LLM_API_KEY를 붙여 넣고 Enter (AI 코멘트를 쓰지 않으면 그냥 Enter): " LLM_KEY
    echo
  fi
  (
    umask 027
    cat >"$ENV_FILE" <<EOF
BASE_URL=${BASE_URL}
DATABASE_URL=sqlite:///${DATA_DIR}/taste_balance.db
SECRET_KEY=${SECRET_KEY}
LLM_API_KEY=${LLM_KEY}
LLM_MODEL=claude-haiku-5-5
EOF
  )
fi
chown "root:$APP_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"

echo "==> 서비스 시작"
install -m 644 "$SOURCE_DIR/deploy/taste-balance.service" /etc/systemd/system/taste-balance.service
systemctl daemon-reload
systemctl enable --quiet taste-balance
systemctl restart taste-balance

healthy=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 1
done
if [[ $healthy -ne 1 ]]; then
  journalctl -u taste-balance -n 30 --no-pager >&2 || true
  die "서비스가 시작되지 않았습니다. 위 로그를 확인해 주세요."
fi

echo "==> 만료 데이터 정리 작업 등록"
install -m 644 "$SOURCE_DIR/deploy/taste-balance-cleanup.cron" /etc/cron.d/taste-balance-cleanup
systemctl enable --quiet cron
systemctl restart cron

echo "==> Nginx 설정"
install_site() {
  sed "s/__ADDRESS__/${ADDRESS}/g" "$SOURCE_DIR/deploy/$1" >/etc/nginx/sites-available/taste-balance
  ln -sf /etc/nginx/sites-available/taste-balance /etc/nginx/sites-enabled/taste-balance
  rm -f /etc/nginx/sites-enabled/default
  nginx -t -q
  systemctl enable --quiet nginx
  systemctl reload-or-restart nginx
}
install -m 644 "$SOURCE_DIR/deploy/nginx-proxy.conf" /etc/nginx/snippets/taste-balance-proxy.conf

if [[ $USE_TLS -eq 1 ]]; then
  if [[ ! -f "/etc/letsencrypt/live/$ADDRESS/fullchain.pem" ]]; then
    echo "==> HTTPS 인증서 발급"
    # 인증서 검증에 80번 포트를 쓰므로 HTTP 설정을 먼저 올린다.
    install_site nginx-http.conf
    certbot_contact=(--register-unsafely-without-email)
    [[ -z "$EMAIL" ]] || certbot_contact=(--email "$EMAIL" --no-eff-email)
    certbot certonly --webroot -w "$WEBROOT" -d "$ADDRESS" --non-interactive --agree-tos \
      --deploy-hook "systemctl reload nginx" "${certbot_contact[@]}" \
      || die "인증서를 받지 못했습니다. 도메인이 이 서버의 IP를 가리키는지, 80번 포트가 열려 있는지 확인해 주세요."
  fi
  install_site nginx-https.conf
else
  install_site nginx-http.conf
fi

echo
echo "설치가 끝났습니다: ${BASE_URL}"
[[ $USE_TLS -eq 1 ]] || echo "HTTP 모드입니다. 실제 사용 전에는 도메인을 연결해 HTTPS로 다시 실행해 주세요."
