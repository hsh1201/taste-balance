import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlmodel import Session

from app.config import settings
from app.db import get_session
from app.models import utcnow
from app.services.cleanup import delete_expired

router = APIRouter(prefix="/api/cron")


@router.get("/cleanup", include_in_schema=False)
def run_cleanup(
    session: Annotated[Session, Depends(get_session)],
    authorization: Annotated[str, Header()] = "",
) -> dict[str, int]:
    """Vercel Cron이 호출한다. CRON_SECRET이 없으면 이 경로 자체가 없는 것처럼 동작한다."""
    if not settings.cron_secret:
        raise HTTPException(status_code=404)
    if not hmac.compare_digest(authorization.encode(), f"Bearer {settings.cron_secret}".encode()):
        raise HTTPException(status_code=401)
    surveys, responses = delete_expired(session, utcnow())
    return {"surveys": surveys, "responses": responses}
