import hashlib
import hmac
import time
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import text
from sqlmodel import Session

from app.config import settings
from app.db import get_session
from app.security import client_ip

# PostgreSQL과 SQLite가 같은 문법을 쓴다. 동시에 들어와도 횟수를 빠뜨리지 않도록 한 문장으로 처리한다.
_COUNT_HIT = text(
    """
    INSERT INTO ratelimithit (bucket, window_start, hits)
    VALUES (:bucket, :window_start, 1)
    ON CONFLICT (bucket, window_start) DO UPDATE SET hits = ratelimithit.hits + 1
    RETURNING hits
    """
)


class SharedRateLimiter:
    """인스턴스끼리 공유되는 고정 구간 rate limit. 생성, 제출처럼 쓰기나 비용이 드는 요청에 쓴다."""

    def __init__(self, name: str, limit: int, window_seconds: int, clock: Callable[[], float] = time.time) -> None:
        self.name = name
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock

    def _bucket(self, ip: str) -> str:
        # 접속자 IP를 DB에 그대로 남기지 않는다.
        digest = hmac.new(settings.secret_key.encode(), ip.encode(), hashlib.sha256).hexdigest()
        return f"{self.name}:{digest[:40]}"

    def allow(self, session: Session, ip: str) -> bool:
        now = int(self._clock())
        window_start = now - now % self.window_seconds
        hits = session.connection().execute(_COUNT_HIT, {"bucket": self._bucket(ip), "window_start": window_start}).scalar_one()
        session.commit()
        return hits <= self.limit


create_limiter = SharedRateLimiter("create", limit=20, window_seconds=600)
submit_limiter = SharedRateLimiter("submit", limit=30, window_seconds=600)
# 호출마다 비용이 들기 때문에 조회보다 훨씬 좁게 잡는다.
ai_comment_limiter = SharedRateLimiter("ai-comment", limit=20, window_seconds=600)


def shared_rate_limit(limiter: SharedRateLimiter) -> Callable[..., None]:
    def dependency(request: Request, session: Annotated[Session, Depends(get_session)]) -> None:
        if not limiter.allow(session, client_ip(request)):
            raise HTTPException(status_code=429, headers={"Retry-After": str(limiter.window_seconds)})

    return dependency
