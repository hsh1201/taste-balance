"""만료된 설문과 응답을 삭제한다.

cron은 작업 폴더가 프로젝트 루트가 아닐 수 있으므로 절대 경로로 실행한다.
    /path/to/venv/bin/python /path/to/scripts/cleanup.py

지우지 않고 개수만 보려면 --dry-run을 붙인다.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

# 프로젝트 루트 밖에서 파일을 직접 실행해도 app을 불러올 수 있게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.models import AiComment, Response, Survey, utcnow  # noqa: E402


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
    """만료된 설문과 응답을 한 트랜잭션으로 지우고 (설문 수, 응답 수)를 돌려준다."""
    counts = count_expired(session, now)
    # 외래 키 설정에 기대지 않도록 딸린 데이터부터 직접 지운다.
    session.exec(delete(AiComment).where(AiComment.survey_id.in_(_expired_survey_ids(now))))
    session.exec(delete(Response).where(Response.survey_id.in_(_expired_survey_ids(now))))
    session.exec(delete(Survey).where(Survey.expires_at <= now))
    session.commit()
    return counts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="만료된 설문과 응답을 삭제합니다.")
    parser.add_argument("--dry-run", action="store_true", help="지우지 않고 삭제 대상 개수만 보여줍니다.")
    args = parser.parse_args(argv)

    # 앱을 한 번도 띄우지 않은 서버에서 cron이 먼저 돌아도 실패하지 않게 한다.
    init_db()
    now = utcnow()
    with Session(engine) as session:
        if args.dry_run:
            surveys, responses = count_expired(session, now)
            print(f"삭제 대상: 설문 {surveys}개, 응답 {responses}개 (--dry-run이라 지우지 않았습니다)")
        else:
            surveys, responses = delete_expired(session, now)
            print(f"삭제 완료: 설문 {surveys}개, 응답 {responses}개")


if __name__ == "__main__":
    main()
