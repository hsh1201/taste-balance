import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import AiComment, Response, Survey, utcnow
from scripts.cleanup import count_expired, delete_expired

CLEANUP_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "cleanup.py"


def add_survey(session: Session, expires_in: timedelta, answered: bool = True) -> Survey:
    survey = Survey(
        recipient_name="민지",
        relation="친구",
        occasion="생일",
        budget_min=30_000,
        budget_max=70_000,
        question_ids=["q01"],
        expires_at=utcnow() + expires_in,
    )
    session.add(survey)
    session.commit()
    session.refresh(survey)
    if answered:
        session.add(
            Response(
                survey_id=survey.id,
                answers=[{"question_id": "q01", "choice": 0}],
                persona_type="emotional_object_solo",
            )
        )
        session.commit()
    return survey


def remaining(session: Session) -> tuple[int, int]:
    session.expire_all()
    return len(session.exec(select(Survey)).all()), len(session.exec(select(Response)).all())


EXPIRED = timedelta(seconds=-1)
ACTIVE = timedelta(days=1)


def test_expired_survey_and_its_response_are_deleted(session):
    add_survey(session, EXPIRED)
    assert delete_expired(session, utcnow()) == (1, 1)
    assert remaining(session) == (0, 0)


def add_comment(session: Session, survey: Survey) -> None:
    session.add(AiComment(survey_id=survey.id, picks=[{"name": "선물", "comment": "좋아요. 정말요."}], model="test"))
    session.commit()


def test_ai_comment_of_an_expired_survey_is_deleted(session):
    add_comment(session, add_survey(session, EXPIRED))
    delete_expired(session, utcnow())
    session.expire_all()
    assert session.exec(select(AiComment)).all() == []


def test_ai_comment_of_an_active_survey_is_kept(session):
    add_comment(session, add_survey(session, ACTIVE))
    add_comment(session, add_survey(session, EXPIRED))
    delete_expired(session, utcnow())
    session.expire_all()
    assert len(session.exec(select(AiComment)).all()) == 1


def test_active_survey_and_its_response_are_kept(session):
    add_survey(session, ACTIVE)
    assert delete_expired(session, utcnow()) == (0, 0)
    assert remaining(session) == (1, 1)


def test_only_expired_ones_are_deleted(session):
    kept = add_survey(session, ACTIVE)
    add_survey(session, EXPIRED)
    add_survey(session, EXPIRED, answered=False)

    assert delete_expired(session, utcnow()) == (2, 1)
    assert remaining(session) == (1, 1)
    assert session.exec(select(Survey)).one().id == kept.id
    assert session.exec(select(Response)).one().survey_id == kept.id


def test_expiry_boundary_matches_what_the_app_treats_as_expired(session):
    survey = add_survey(session, ACTIVE)
    moment = survey.expires_at

    assert delete_expired(session, moment - timedelta(microseconds=1)) == (0, 0)
    assert delete_expired(session, moment) == (1, 1)


def test_count_does_not_delete(session):
    add_survey(session, EXPIRED)
    add_survey(session, EXPIRED, answered=False)
    assert count_expired(session, utcnow()) == (2, 1)
    assert remaining(session) == (2, 1)


def test_nothing_to_delete(session):
    assert delete_expired(session, utcnow()) == (0, 0)


def test_links_of_a_deleted_survey_are_gone(client, session):
    survey = add_survey(session, EXPIRED)
    slug, token = survey.slug, survey.admin_token
    assert client.get(f"/g/{slug}").status_code == 410
    assert client.get(f"/r/{token}").status_code == 410

    delete_expired(session, utcnow())

    assert client.get(f"/g/{slug}").status_code == 404
    assert client.get(f"/r/{token}").status_code == 404


# 아래는 cron처럼 다른 작업 폴더에서 파일을 직접 실행해 실제 DB 파일에 적용한다.


@pytest.fixture
def database(tmp_path) -> Path:
    return tmp_path / "cron.db"


def run_script(database: Path, working_dir: Path, *arguments: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "BASE_URL": "https://test.example",
        "DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "SECRET_KEY": "test-secret-key-that-is-long-enough-for-settings",
        "PYTHONIOENCODING": "utf-8",
    }
    return subprocess.run(
        [sys.executable, str(CLEANUP_SCRIPT), *arguments],
        cwd=working_dir,
        env=env,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


@pytest.fixture
def filled_database(database):
    file_engine = create_engine(f"sqlite:///{database.as_posix()}")
    SQLModel.metadata.create_all(file_engine)
    with Session(file_engine) as session:
        add_survey(session, EXPIRED)
        add_survey(session, ACTIVE)
    yield file_engine
    file_engine.dispose()


def test_script_deletes_from_any_working_directory(filled_database, database, tmp_path):
    result = run_script(database, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "삭제 완료: 설문 1개, 응답 1개" in result.stdout
    with Session(filled_database) as session:
        assert remaining(session) == (1, 1)


def test_script_dry_run_deletes_nothing(filled_database, database, tmp_path):
    result = run_script(database, tmp_path, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "삭제 대상: 설문 1개, 응답 1개" in result.stdout
    with Session(filled_database) as session:
        assert remaining(session) == (2, 2)


def test_script_works_before_the_app_has_ever_run(database, tmp_path):
    result = run_script(database, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "삭제 완료: 설문 0개, 응답 0개" in result.stdout
