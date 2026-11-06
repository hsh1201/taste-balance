import hashlib
import hmac
import os
import secrets
import string
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import HTTPException, Request, Response

from app.config import settings

SLUG_LENGTH = 8
SLUG_ALPHABET = string.ascii_letters + string.digits
ADMIN_TOKEN_BYTES = 32

CSRF_COOKIE = "tb_csrf"
CSRF_COOKIE_MAX_AGE = 60 * 60 * 24
DONE_COOKIE = "tb_done"
DONE_COOKIE_MAX_AGE = 60 * 60 * 24 * 30


def generate_slug() -> str:
    return "".join(secrets.choice(SLUG_ALPHABET) for _ in range(SLUG_LENGTH))


def generate_admin_token() -> str:
    return secrets.token_urlsafe(ADMIN_TOKEN_BYTES)


def _sign(value: str) -> str:
    return hmac.new(settings.secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()


def csrf_nonce(request: Request) -> str:
    return request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(16)


def csrf_token_for(nonce: str) -> str:
    return _sign(f"csrf:{nonce}")


def set_csrf_cookie(response: Response, nonce: str) -> None:
    response.set_cookie(
        CSRF_COOKIE,
        nonce,
        max_age=CSRF_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


def is_valid_csrf(request: Request, token: str) -> bool:
    nonce = request.cookies.get(CSRF_COOKIE)
    if not nonce or not token:
        return False
    return hmac.compare_digest(csrf_token_for(nonce).encode(), token.encode())


def set_done_cookie(response: Response, slug: str) -> None:
    """게임을 끝낸 브라우저에서만 내 결과를 다시 볼 수 있게 표시한다."""
    response.set_cookie(
        DONE_COOKIE,
        _sign(f"done:{slug}"),
        max_age=DONE_COOKIE_MAX_AGE,
        path=f"/g/{slug}",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


def has_done_cookie(request: Request, slug: str) -> bool:
    value = request.cookies.get(DONE_COOKIE, "")
    return hmac.compare_digest(_sign(f"done:{slug}").encode(), value.encode())


class RateLimiter:
    """IP별 슬라이딩 윈도우. 프로세스 메모리에 저장하므로 워커 1개를 전제로 한다."""

    MAX_TRACKED_KEYS = 10_000

    def __init__(self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        cutoff = now - self.window_seconds
        with self._lock:
            if len(self._hits) >= self.MAX_TRACKED_KEYS:
                self._forget_idle_keys(cutoff)
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def _forget_idle_keys(self, cutoff: float) -> None:
        for key in [key for key, hits in self._hits.items() if not hits or hits[-1] <= cutoff]:
            del self._hits[key]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


# 페이지 조회용 가벼운 제한이다. 서버리스에서는 인스턴스별로 따로 세므로 대략적인 보호만 한다.
# 쓰기와 비용이 드는 요청은 app/rate_limit.py에서 DB로 센다.
lookup_limiter = RateLimiter(limit=120, window_seconds=60)


def client_ip(request: Request) -> str:
    # Vercel은 접속자가 보낸 값을 덮어쓰므로 이 헤더를 믿을 수 있다.
    # 다른 환경에서는 위조할 수 있어 쓰지 않고, Uvicorn이 믿을 만한 프록시 값만 반영한 주소를 쓴다.
    if os.environ.get("VERCEL"):
        forwarded = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(limiter: RateLimiter) -> Callable[[Request], None]:
    def dependency(request: Request) -> None:
        if not limiter.allow(client_ip(request)):
            raise HTTPException(status_code=429, headers={"Retry-After": str(int(limiter.window_seconds))})

    return dependency
