import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from app.models import Survey, utcnow
from app.rate_limit import create_limiter
from app.security import RateLimiter
from app.services.questions import load_questions
from app.templating import format_kst_date, format_won

VALID_FORM = {
    "recipient_name": "민지",
    "relation": "친구",
    "occasion": "생일",
    "budget_min": "30000",
    "budget_max": "70000",
}


def csrf_from_form(client) -> str:
    html = client.get("/new").text
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def submit(client, **overrides):
    form = VALID_FORM | {"csrf_token": csrf_from_form(client)} | overrides
    return client.post("/new", data=form, follow_redirects=False)


def create(client, session) -> Survey:
    response = submit(client)
    assert response.status_code == 303
    return session.exec(select(Survey)).one()


def test_home_has_start_button(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "선물 조사 시작" in response.text
    assert 'href="/new"' in response.text


def test_create_survey_stores_values_and_redirects_to_links(client, session):
    response = submit(client)
    survey = session.exec(select(Survey)).one()

    assert response.status_code == 303
    assert response.headers["location"] == f"/r/{survey.admin_token}/links"
    assert survey.recipient_name == "민지"
    assert survey.relation == "친구"
    assert survey.occasion == "생일"
    assert (survey.budget_min, survey.budget_max) == (30_000, 70_000)
    assert len(survey.question_ids) == 15
    assert set(survey.question_ids) <= set(load_questions())


def test_links_page_builds_both_links_from_base_url(client, session):
    survey = create(client, session)
    html = client.get(f"/r/{survey.admin_token}/links").text

    assert f"https://test.example/g/{survey.slug}" in html
    assert f"https://test.example/r/{survey.admin_token}" in html
    assert "꼭 저장하세요" in html
    assert "localhost" not in html
    assert "127.0.0.1" not in html


def test_result_page_before_response_shows_pending(client, session):
    survey = create(client, session)
    response = client.get(f"/r/{survey.admin_token}")

    assert response.status_code == 200
    assert "아직 응답 전이에요" in response.text
    assert f"https://test.example/g/{survey.slug}" in response.text


@pytest.mark.parametrize("path", ["/r/{token}", "/r/{token}/links"])
def test_result_pages_are_private(client, session, path):
    survey = create(client, session)
    response = client.get(path.format(token=survey.admin_token))

    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert '<meta name="robots" content="noindex, nofollow">' in response.text


def test_share_slug_cannot_open_result_page(client, session):
    survey = create(client, session)
    assert client.get(f"/r/{survey.slug}").status_code == 404


@pytest.mark.parametrize("path", ["/r/unknown-token", "/r/unknown-token/links"])
def test_unknown_token_returns_404(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert "없는 링크" in response.text


@pytest.mark.parametrize("path", ["/r/{token}", "/r/{token}/links"])
def test_expired_survey_returns_410(client, session, path):
    survey = create(client, session)
    survey.expires_at = utcnow() - timedelta(seconds=1)
    session.add(survey)
    session.commit()

    response = client.get(path.format(token=survey.admin_token))
    assert response.status_code == 410
    assert "기간이 지난 링크" in response.text


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"recipient_name": "   "}, "별명을 입력"),
        ({"recipient_name": "님"}, "별명을 입력"),
        ({"recipient_name": "가" * 21}, "20자까지"),
        ({"relation": "원수"}, "관계를"),
        ({"occasion": "개업"}, "상황을"),
        ({"budget_min": "abc"}, "예산 범위"),
        ({"budget_min": "70000", "budget_max": "70000"}, "예산 범위"),
        ({"budget_min": "80000", "budget_max": "70000"}, "예산 범위"),
        ({"budget_min": "0"}, "예산 범위"),
        ({"budget_max": "310000"}, "예산 범위"),
    ],
)
def test_invalid_form_is_rejected(client, session, overrides, message):
    response = submit(client, **overrides)

    assert response.status_code == 422
    assert message in response.text
    assert session.exec(select(Survey)).all() == []


def test_invalid_form_keeps_what_user_typed(client):
    response = submit(client, relation="", recipient_name="민지")
    assert 'value="민지"' in response.text


@pytest.mark.parametrize(
    ("typed", "stored"),
    [("민지님", "민지"), ("  김 민지  ", "김 민지"), ("민\n지", "민 지"), ("가" * 20 + "님", "가" * 20)],
)
def test_recipient_name_is_normalized(client, session, typed, stored):
    submit(client, recipient_name=typed)
    assert session.exec(select(Survey)).one().recipient_name == stored


def test_recipient_name_is_escaped(client, session):
    submit(client, recipient_name="<script>x</script>")
    survey = session.exec(select(Survey)).one()
    html = client.get(f"/r/{survey.admin_token}").text

    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


def test_post_without_csrf_token_is_rejected(client, session):
    client.get("/new")
    response = client.post("/new", data=VALID_FORM, follow_redirects=False)

    assert response.status_code == 403
    assert session.exec(select(Survey)).all() == []


def test_post_with_forged_csrf_token_is_rejected(client, session):
    client.get("/new")
    response = client.post("/new", data=VALID_FORM | {"csrf_token": "위조된토큰"}, follow_redirects=False)

    assert response.status_code == 403
    assert session.exec(select(Survey)).all() == []


def test_csrf_token_requires_matching_cookie(client, session):
    token = csrf_from_form(client)
    client.cookies.clear()
    response = client.post("/new", data=VALID_FORM | {"csrf_token": token}, follow_redirects=False)

    assert response.status_code == 403
    assert session.exec(select(Survey)).all() == []


def test_create_is_rate_limited(client, monkeypatch):
    monkeypatch.setattr(create_limiter, "limit", 2)
    assert submit(client).status_code == 303
    assert submit(client).status_code == 303

    response = submit(client)
    assert response.status_code == 429
    assert "retry-after" in response.headers


def test_rate_limiter_recovers_after_window():
    now = [0.0]
    limiter = RateLimiter(limit=2, window_seconds=10, clock=lambda: now[0])

    assert limiter.allow("a") is True
    assert limiter.allow("a") is True
    assert limiter.allow("a") is False
    assert limiter.allow("b") is True

    now[0] = 10.0
    assert limiter.allow("a") is True


def test_rate_limiter_forgets_idle_keys(monkeypatch):
    now = [0.0]
    limiter = RateLimiter(limit=1, window_seconds=10, clock=lambda: now[0])
    monkeypatch.setattr(RateLimiter, "MAX_TRACKED_KEYS", 3)
    for key in "abc":
        limiter.allow(key)

    now[0] = 20.0
    limiter.allow("d")
    assert set(limiter._hits) == {"d"}


def test_kst_date_uses_korean_calendar_day():
    assert format_kst_date(datetime(2026, 12, 4, 15, 0, tzinfo=UTC)) == "2026년 12월 5일"


@pytest.mark.parametrize(("amount", "text"), [(30_000, "3만 원"), (300_000, "30만 원"), (15_500, "15,500원")])
def test_format_won(amount, text):
    assert format_won(amount) == text


def test_health_check_answers(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_docs_are_disabled(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
