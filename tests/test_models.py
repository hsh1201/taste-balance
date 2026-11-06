from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.models import Response, Survey, utcnow


def make_survey(**overrides) -> Survey:
    values = {
        "recipient_name": "민지",
        "relation": "친구",
        "occasion": "생일",
        "budget_min": 30_000,
        "budget_max": 70_000,
        "question_ids": ["q01", "q02"],
    }
    return Survey(**(values | overrides))


def make_response(survey_id: int) -> Response:
    return Response(
        survey_id=survey_id,
        answers=[{"question_id": "q01", "choice": 0}],
        persona_type="emotional_object_solo",
    )


def test_survey_generates_links_and_expiry(session):
    survey = make_survey()
    session.add(survey)
    session.commit()
    session.refresh(survey)

    assert len(survey.slug) == 8
    assert survey.slug.isalnum()
    assert len(survey.admin_token) >= 32
    assert survey.expires_at - survey.created_at == pytest.approx(timedelta(days=30), abs=timedelta(seconds=5))
    assert survey.question_ids == ["q01", "q02"]
    assert survey.is_expired is False


def test_generated_links_are_not_repeated():
    surveys = [make_survey() for _ in range(200)]
    assert len({survey.slug for survey in surveys}) == 200
    assert len({survey.admin_token for survey in surveys}) == 200


def test_survey_past_expiry_is_expired():
    assert make_survey(expires_at=utcnow() - timedelta(seconds=1)).is_expired is True


def test_duplicate_slug_is_rejected(session):
    session.add(make_survey(slug="sameslug"))
    session.commit()
    session.add(make_survey(slug="sameslug"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_survey_allows_only_one_response(session):
    survey = make_survey()
    session.add(survey)
    session.commit()

    session.add(make_response(survey.id))
    session.commit()
    session.add(make_response(survey.id))
    with pytest.raises(IntegrityError):
        session.commit()


def test_deleting_survey_deletes_its_response(session):
    survey = make_survey()
    session.add(survey)
    session.commit()
    session.add(make_response(survey.id))
    session.commit()

    session.delete(survey)
    session.commit()

    assert session.exec(select(Response)).all() == []
