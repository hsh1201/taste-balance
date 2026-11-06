from datetime import datetime

from sqlalchemy import delete, func
from sqlmodel import Session, select

from app.models import AiComment, RateLimitHit, Response, Survey

# 가장 긴 제한 구간(10분)보다 넉넉하면 된다.
RATE_LIMIT_RETENTION_SECONDS = 24 * 60 * 60


def _expired_survey_ids(now: datetime):
    # 경계값은 Survey.is_expired와 같다. 앱이 만료로 보는 설문만 지운다.
    return select(Survey.id).where(Survey.expires_at <= now)


def count_expired(session: Session, now: datetime) -> tuple[int, int]:
    """삭제 대상인 (설문 수, 응답 수)를 돌려준다."""
    surveys = session.exec(select(func.count()).select_from(Survey).where(Survey.expires_at <= now)).one()
    responses = session.exec(
        select(func.count()).select_from(Response).where(Response.survey_id.in_(_expired_survey_ids(now)))
    ).one()
    return surveys, responses


def delete_expired(session: Session, now: datetime) -> tuple[int, int]:
    """만료된 설문과 응답을 한 트랜잭션으로 지우고 (설문 수, 응답 수)를 돌려준다.

    여러 번 실행해도 안전하다. Vercel Cron은 같은 시각에 두 번 호출하거나 한 번 건너뛸 수 있다.
    """
    counts = count_expired(session, now)
    # 외래 키 설정에 기대지 않도록 딸린 데이터부터 직접 지운다.
    session.exec(delete(AiComment).where(AiComment.survey_id.in_(_expired_survey_ids(now))))
    session.exec(delete(Response).where(Response.survey_id.in_(_expired_survey_ids(now))))
    session.exec(delete(Survey).where(Survey.expires_at <= now))
    session.exec(delete(RateLimitHit).where(RateLimitHit.window_start < int(now.timestamp()) - RATE_LIMIT_RETENTION_SECONDS))
    session.commit()
    return counts
