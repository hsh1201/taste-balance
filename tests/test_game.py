import json
import re
from datetime import timedelta

import pytest
from sqlmodel import select

from app.models import Response, Survey, utcnow
from app.rate_limit import submit_limiter
from app.security import DONE_COOKIE
from app.services.persona import load_personas
from app.services.questions import pick_question_ids

NOTICE = "이 게임 결과는 링크를 보낸 친구에게도 전달돼요 🎁 (왜인지는 비밀!)"
SECRET_WORDS = ("선물", "조사", "예산", "gift", "budget")


@pytest.fixture
def survey(session) -> Survey:
    created = Survey(
        recipient_name="민지",
        relation="연인",
        occasion="기념일",
        budget_min=30_000,
        budget_max=70_000,
        question_ids=pick_question_ids(),
    )
    session.add(created)
    session.commit()
    session.refresh(created)
    return created


def full_answers(survey: Survey, choice: int = 0) -> list[dict]:
    return [{"question_id": question_id, "choice": choice} for question_id in survey.question_ids]


def submit(client, survey: Survey, answers: list[dict] | None = None):
    payload = {"answers": full_answers(survey) if answers is None else answers}
    return client.post(f"/g/{survey.slug}/submit", json=payload)


def game_data(html: str) -> list[dict]:
    return json.loads(re.search(r'id="game-data">(.*?)</script>', html, re.S).group(1))


def stored_responses(session) -> list[Response]:
    return session.exec(select(Response)).all()


def test_intro_shows_recipient_name(client, survey):
    response = client.get(f"/g/{survey.slug}")
    assert response.status_code == 200
    assert "민지님의" in response.text
    assert "취향 밸런스 게임" in response.text
    assert 'data-action="start"' in response.text


def test_game_sends_questions_in_order_without_tags(client, survey):
    questions = game_data(client.get(f"/g/{survey.slug}").text)

    assert [question["id"] for question in questions] == survey.question_ids
    for question in questions:
        assert set(question) == {"id", "text", "choices"}
        assert len(question["choices"]) == 2
        for choice in question["choices"]:
            assert set(choice) == {"label", "emoji"}


def test_game_page_hides_private_survey_details(client, survey):
    html = client.get(f"/g/{survey.slug}").text

    assert survey.admin_token not in html
    assert survey.occasion not in html
    assert "30000" not in html
    assert "70000" not in html
    assert '"tags"' not in html
    assert "kind" not in html


@pytest.mark.parametrize("word", SECRET_WORDS)
def test_game_pages_never_reveal_the_purpose(client, survey, word):
    pages = [client.get(f"/g/{survey.slug}").text]
    submit(client, survey)
    pages.append(client.get(f"/g/{survey.slug}/result").text)
    pages.append(client.get(f"/g/{survey.slug}").text)
    for page in pages:
        assert word not in page


@pytest.mark.parametrize("path", ["/static/js/game.js", "/static/js/card.js", "/static/css/style.css"])
@pytest.mark.parametrize("word", SECRET_WORDS)
def test_game_assets_never_reveal_the_purpose(client, path, word):
    response = client.get(path)
    assert response.status_code == 200
    assert word not in response.text


def test_game_pages_only_load_shared_assets(client, survey):
    pages = [client.get(f"/g/{survey.slug}").text]
    submit(client, survey)
    pages += [client.get(f"/g/{survey.slug}/result").text, client.get(f"/g/{survey.slug}").text]
    for page in pages:
        assert "surveyor.css" not in page
        assert "form.js" not in page
        assert "copy.js" not in page


def test_share_preview_tags_use_recipient_name_and_base_url(client, survey):
    html = client.get(f"/g/{survey.slug}").text

    assert '<meta property="og:title" content="민지님을 위한 취향 밸런스 게임 🎮">' in html
    assert f'<meta property="og:url" content="https://test.example/g/{survey.slug}">' in html
    assert '<meta property="og:image" content="https://test.example/static/img/og.png">' in html
    assert client.get("/static/img/og.png").status_code == 200


def test_share_preview_tags_escape_recipient_name(client, session):
    tricky = Survey(
        recipient_name='"><script>',
        relation="친구",
        occasion="생일",
        budget_min=10_000,
        budget_max=20_000,
        question_ids=pick_question_ids(),
    )
    session.add(tricky)
    session.commit()
    html = client.get(f"/g/{tricky.slug}").text

    assert '"><script>' not in html
    assert "&#34;&gt;&lt;script&gt;" in html


def test_opening_the_link_does_not_use_up_the_game(client, session, survey):
    """카카오톡 미리보기 수집기가 먼저 열어도 게임이 소진되면 안 된다."""
    for _ in range(3):
        client.get(f"/g/{survey.slug}")
    assert stored_responses(session) == []
    assert 'data-action="start"' in client.get(f"/g/{survey.slug}").text


def test_submit_stores_answers_and_persona(client, session, survey):
    response = submit(client, survey, list(reversed(full_answers(survey, choice=1))))

    assert response.status_code == 200
    assert response.json() == {"next": f"/g/{survey.slug}/result"}
    stored = stored_responses(session)[0]
    assert stored.survey_id == survey.id
    # 보낸 순서와 상관없이 게임 순서대로 저장한다.
    assert stored.answers == full_answers(survey, choice=1)
    assert stored.persona_type in load_personas()
    assert stored.completed_at is not None


def test_result_page_shows_persona_and_notice(client, session, survey):
    submit(client, survey)
    stored = stored_responses(session)[0]
    persona = load_personas()[stored.persona_type]

    html = client.get(f"/g/{survey.slug}/result").text
    assert "당신은" in html
    assert f"{persona['name']}형!" in html
    assert persona["description"] in html
    assert 'data-action="save-image"' in html
    assert NOTICE in html


def test_second_submit_is_rejected_and_keeps_first_answers(client, session, survey):
    assert submit(client, survey, full_answers(survey, choice=0)).status_code == 200
    assert submit(client, survey, full_answers(survey, choice=1)).status_code == 409

    stored = stored_responses(session)
    assert len(stored) == 1
    assert stored[0].answers == full_answers(survey, choice=0)


def test_revisit_shows_already_completed_with_link_to_own_result(client, survey):
    submit(client, survey)
    html = client.get(f"/g/{survey.slug}").text

    assert "이미 완료한 게임이에요" in html
    assert "game-data" not in html
    assert f'href="/g/{survey.slug}/result"' in html
    assert NOTICE in html


def test_other_browser_sees_completed_page_but_not_the_result(client, session, survey):
    submit(client, survey)
    persona_name = load_personas()[stored_responses(session)[0].persona_type]["name"]
    client.cookies.clear()

    html = client.get(f"/g/{survey.slug}").text
    assert "이미 완료한 게임이에요" in html
    assert persona_name not in html
    assert f'href="/g/{survey.slug}/result"' not in html

    response = client.get(f"/g/{survey.slug}/result", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/g/{survey.slug}"


def test_forged_done_cookie_cannot_open_result(client, survey):
    submit(client, survey)
    client.cookies.clear()
    client.cookies.set(DONE_COOKIE, "forged", domain="test.example", path=f"/g/{survey.slug}")

    response = client.get(f"/g/{survey.slug}/result", follow_redirects=False)
    assert response.status_code == 303


def test_done_cookie_is_scoped_to_the_game(client, survey):
    cookie = submit(client, survey).headers["set-cookie"]
    assert f"Path=/g/{survey.slug}" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie


def test_result_before_finishing_returns_to_game(client, survey):
    response = client.get(f"/g/{survey.slug}/result", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/g/{survey.slug}"


def invalid_answers(survey: Survey) -> dict[str, list[dict]]:
    answers = full_answers(survey)
    return {
        "empty": [],
        "missing_one": answers[:-1],
        "duplicate_replaces_one": [*answers[:-1], answers[0]],
        "unknown_question": [*answers[:-1], {"question_id": "nope", "choice": 0}],
        "too_many": [*answers, {"question_id": "nope", "choice": 0}],
    }


@pytest.mark.parametrize(
    "case", ["empty", "missing_one", "duplicate_replaces_one", "unknown_question", "too_many"]
)
def test_incomplete_or_wrong_questions_are_rejected(client, session, survey, case):
    response = submit(client, survey, invalid_answers(survey)[case])
    assert response.status_code == 422
    assert stored_responses(session) == []


def test_question_from_outside_this_game_is_rejected(client, session, survey):
    other = next(qid for qid in ("q01", "q02", "q03", "f01", "f02") if qid not in survey.question_ids)
    answers = [*full_answers(survey)[:-1], {"question_id": other, "choice": 0}]
    assert submit(client, survey, answers).status_code == 422
    assert stored_responses(session) == []


@pytest.mark.parametrize("choice", [2, -1, "0", None, True])
def test_invalid_choice_is_rejected(client, session, survey, choice):
    answers = full_answers(survey)
    answers[0]["choice"] = choice
    assert submit(client, survey, answers).status_code == 422
    assert stored_responses(session) == []


@pytest.mark.parametrize("headers", [{"Content-Type": "text/plain"}, {"Content-Type": ""}, {}])
def test_cross_site_form_style_submit_is_rejected(client, session, survey, headers):
    """다른 사이트가 사전 요청 없이 보낼 수 있는 형식은 받지 않는다."""
    body = json.dumps({"answers": full_answers(survey)})
    response = client.post(f"/g/{survey.slug}/submit", content=body, headers=headers)
    assert response.status_code == 422
    assert stored_responses(session) == []


@pytest.mark.parametrize("path", ["/g/nothing1", "/g/nothing1/result"])
def test_unknown_slug_returns_404_without_revealing_the_purpose(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert "없는 링크" in response.text
    assert "선물" not in response.text
    assert 'href="/new"' not in response.text


def test_submit_to_unknown_slug_returns_404(client):
    assert client.post("/g/nothing1/submit", json={"answers": []}).status_code == 404


def test_expired_game_is_closed(client, session, survey):
    answers = full_answers(survey)
    survey.expires_at = utcnow() - timedelta(seconds=1)
    session.add(survey)
    session.commit()

    response = client.get(f"/g/{survey.slug}")
    assert response.status_code == 410
    assert "선물" not in response.text
    assert client.post(f"/g/{survey.slug}/submit", json={"answers": answers}).status_code == 410
    assert stored_responses(session) == []


def test_game_pages_are_noindex_but_open_to_preview_bots(client, survey):
    response = client.get(f"/g/{survey.slug}")
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert response.headers["cache-control"] == "no-store"


def test_submit_is_rate_limited(client, survey, monkeypatch):
    monkeypatch.setattr(submit_limiter, "limit", 1)
    assert submit(client, survey, []).status_code == 422
    assert submit(client, survey).status_code == 429


def test_result_link_reports_response_instead_of_pending(client, session, survey):
    assert "아직 응답 전이에요" in client.get(f"/r/{survey.admin_token}").text
    submit(client, survey)
    persona_name = load_personas()[stored_responses(session)[0].persona_type]["name"]

    html = client.get(f"/r/{survey.admin_token}").text
    assert "아직 응답 전이에요" not in html
    assert persona_name in html
