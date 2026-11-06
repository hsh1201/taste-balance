import json
import random
import re
from html import unescape
from pathlib import Path

import pytest
from sqlmodel import select

from app.models import Response, Survey
from app.services.persona import determine_persona, load_personas
from app.services.questions import pick_question_ids
from app.services.recommend import RECOMMENDATION_COUNT, build_report, load_gifts
from app.templating import format_won_range

BUDGET = (30_000, 70_000)
STATIC_DIR = Path(__file__).resolve().parent.parent / "app" / "static"
GIFT_NAMES = [gift["name"] for gift in load_gifts()]


def make_answers(seed: int = 1) -> tuple[list[str], list[dict]]:
    rng = random.Random(seed)
    question_ids = pick_question_ids(rng)
    return question_ids, [{"question_id": qid, "choice": rng.randint(0, 1)} for qid in question_ids]


def add_survey(session, seed: int = 1, budget: tuple[int, int] = BUDGET, name: str = "민지", answers=None) -> Survey:
    question_ids, generated = make_answers(seed)
    survey = Survey(
        recipient_name=name,
        relation="연인",
        occasion="기념일",
        budget_min=budget[0],
        budget_max=budget[1],
        question_ids=question_ids,
    )
    session.add(survey)
    session.commit()
    session.refresh(survey)
    stored = answers or generated
    session.add(Response(survey_id=survey.id, answers=stored, persona_type=determine_persona(stored)))
    session.commit()
    return survey


@pytest.fixture
def answered(session) -> Survey:
    return add_survey(session)


@pytest.fixture
def expected() -> dict:
    return build_report(make_answers()[1], *BUDGET)


def page(client, survey: Survey) -> str:
    response = client.get(f"/r/{survey.admin_token}")
    assert response.status_code == 200
    return response.text


def radar_json(html: str) -> list[dict]:
    return json.loads(re.search(r'id="radar-data">(.*?)</script>', html, re.S).group(1))


def card_names(html: str) -> list[str]:
    return [unescape(name) for name in re.findall(r'class="gift-name">(.*?)</h3>', html)]


def test_page_shows_persona_and_survey_context(client, session, answered):
    persona = load_personas()[determine_persona(make_answers()[1])]
    text = unescape(page(client, answered))

    assert "민지님은" in text
    assert f"{persona['name']}형!" in text
    assert persona["tagline"] in text
    assert persona["description"] in text
    assert "연인" in text
    assert "기념일" in text
    assert "3만~7만 원" in text
    assert "아직 응답 전이에요" not in text


def test_page_shows_eight_cards_in_score_order(client, answered, expected):
    html = page(client, answered)
    assert card_names(html) == [card["name"] for card in expected["recommendations"]]
    assert len(card_names(html)) == RECOMMENDATION_COUNT
    assert html.count('class="gift-card"') == RECOMMENDATION_COUNT
    assert html.count("가장 잘 맞아요") == 1


def test_each_card_shows_reason_category_and_price(client, answered, expected):
    text = unescape(page(client, answered))
    for card in expected["recommendations"]:
        assert card["reason"] in text
        assert f"{card['category_label']} · {format_won_range(card['price_min'], card['price_max'])}" in text
        assert card["emoji"] in text


def test_each_card_has_a_safe_shopping_link(client, answered, expected):
    html = page(client, answered)
    links = re.findall(r'<a [^>]*href="(https://search\.shopping\.naver\.com[^"]*)"[^>]*>', html)
    assert links == [card["shopping_url"] for card in expected["recommendations"]]
    # 새 창으로 넘어가는 쇼핑 사이트에 비밀 결과 링크가 새지 않아야 한다.
    for tag in re.findall(r"<a [^>]*search\.shopping\.naver\.com[^>]*>", html):
        assert 'target="_blank"' in tag
        assert 'rel="noopener noreferrer"' in tag
    assert html.count("쇼핑 검색</a>") == RECOMMENDATION_COUNT


def test_page_shows_keywords_and_things_to_avoid(client, answered, expected):
    text = unescape(page(client, answered))
    assert expected["keywords"]
    for keyword in expected["keywords"]:
        assert f"#{keyword}" in text
    assert "이건 피하세요" in text
    assert 1 <= len(expected["avoid"]) <= 2
    for item in expected["avoid"]:
        assert item["label"] in text
        assert item["reason"] in text


def test_radar_data_is_ready_for_the_chart(client, answered, expected):
    html = page(client, answered)
    axes = radar_json(html)
    assert axes == expected["radar"]
    assert len(axes) == 6
    assert 'id="radar-chart"' in html
    label = unescape(re.search(r'id="radar-chart"[^>]*aria-label="([^"]*)"', html, re.S).group(1))
    for axis in axes:
        assert f"{axis['lean_label']} {axis['lean_value']}퍼센트" in label


def test_taste_is_readable_without_the_chart(client, answered, expected):
    """CDN이 막히거나 스크립트가 꺼져도 축 목록은 서버에서 그린 그대로 보인다."""
    html = page(client, answered)
    assert re.search(r"<div class=\"radar-wrap\" data-radar hidden>", html)
    rows = re.findall(r'<li class="axis-row">(.*?)</li>', html, re.S)
    assert len(rows) == 6
    for row, axis in zip(rows, expected["radar"], strict=True):
        assert axis["left"] in row
        assert axis["right"] in row
        assert f'cx="{axis["value"]}%"' in row


def test_stronger_pole_is_emphasized(client, session):
    answers = [{"question_id": "q10", "choice": 1}, {"question_id": "q22", "choice": 1}]
    html = page(client, add_survey(session, answers=answers))
    assert '<span class="axis-pole is-right is-strong">함께</span>' in html
    assert '<span class="axis-pole">혼자</span>' in html


def test_chart_library_is_pinned_and_integrity_checked(client, answered):
    """결과 페이지 주소에 비밀 토큰이 있어, 외부 스크립트가 변조되면 실행되지 않게 막는다."""
    html = page(client, answered)
    tag = re.search(r'<script src="https://cdn\.jsdelivr\.net/npm/chart\.js@[^>]*>', html, re.S).group(0)
    assert re.search(r"chart\.js@\d+\.\d+\.\d+/", tag)
    assert 'integrity="sha384-' in tag
    assert 'crossorigin="anonymous"' in tag
    assert html.index("chart.umd.min.js") < html.index("/static/js/report.js")


def test_pending_page_does_not_load_the_chart_library(client, session):
    survey = Survey(
        recipient_name="민지",
        relation="친구",
        occasion="생일",
        budget_min=BUDGET[0],
        budget_max=BUDGET[1],
        question_ids=make_answers()[0],
    )
    session.add(survey)
    session.commit()
    html = page(client, survey)
    assert "아직 응답 전이에요" in html
    assert "chart.js" not in html


def test_page_obeys_the_content_security_policy(client, answered):
    """CSP가 인라인 스타일과 인라인 스크립트를 막으므로, 쓰면 화면이 조용히 깨진다."""
    html = page(client, answered)
    assert not re.search(r"<[^>]+\sstyle=", html)
    assert not re.search(r"<[^>]+\son[a-z]+=", html)
    for attributes in re.findall(r"<script([^>]*)>", html):
        source = re.search(r'src="([^"]+)"', attributes)
        if source:
            assert source.group(1).startswith(("/static/", "https://cdn.jsdelivr.net/"))
        else:
            assert 'type="application/json"' in attributes


def test_script_and_page_agree_on_element_names(client, answered):
    html = page(client, answered)
    script = (STATIC_DIR / "js" / "report.js").read_text(encoding="utf-8")
    for needle, marker in [("radar-chart", 'id="radar-chart"'), ("radar-data", 'id="radar-data"'), ("[data-radar]", "data-radar")]:
        assert needle in script
        assert marker in html


@pytest.mark.parametrize("path", ["/static/js/report.js", "/static/css/result.css"])
def test_assets_are_served(client, path):
    assert client.get(path).status_code == 200


def test_page_is_private(client, answered):
    response = client.get(f"/r/{answered.admin_token}")
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert '<meta name="robots" content="noindex, nofollow">' in response.text


def test_recipient_name_is_escaped(client, session):
    html = page(client, add_survey(session, name="<b>민지</b>"))
    assert "<b>민지</b>" not in html
    assert "&lt;b&gt;민지&lt;/b&gt;" in html


def test_reloading_shows_the_same_result(client, answered):
    assert page(client, answered) == page(client, answered)


def test_budget_of_the_survey_decides_the_gifts(client, session):
    cheap = page(client, add_survey(session, budget=(10_000, 20_000)))
    expensive = page(client, add_survey(session, budget=(250_000, 300_000)))
    assert card_names(cheap) != card_names(expensive)
    assert "1만~2만 원" in cheap
    assert "25만~30만 원" in expensive


def test_page_survives_answers_to_questions_removed_from_the_pool(client, session):
    survey = add_survey(session, answers=[{"question_id": "removed", "choice": 0}])
    assert len(card_names(page(client, survey))) == RECOMMENDATION_COUNT


def test_result_saved_with_a_retired_persona_still_opens(client, session):
    """유형 구성이 바뀌기 전에 저장된 응답도 결과 링크가 깨지면 안 된다."""
    survey = add_survey(session)
    stored = session.exec(select(Response).where(Response.survey_id == survey.id)).one()
    stored.persona_type = "emotional_homebody"
    session.add(stored)
    session.commit()

    text = unescape(page(client, survey))
    assert f"{load_personas()[determine_persona(stored.answers)]['name']}형!" in text
    assert len(card_names(text)) == RECOMMENDATION_COUNT


def test_respondent_result_saved_with_a_retired_persona_still_opens(client, session):
    question_ids, answers = make_answers()
    survey = Survey(
        recipient_name="민지",
        relation="연인",
        occasion="기념일",
        budget_min=BUDGET[0],
        budget_max=BUDGET[1],
        question_ids=question_ids,
    )
    session.add(survey)
    session.commit()
    assert client.post(f"/g/{survey.slug}/submit", json={"answers": answers}).status_code == 200
    stored = session.exec(select(Response).where(Response.survey_id == survey.id)).one()
    stored.persona_type = "practical_social"
    session.add(stored)
    session.commit()

    response = client.get(f"/g/{survey.slug}/result")
    assert response.status_code == 200
    assert f"{load_personas()[determine_persona(answers)]['name']}형!" in response.text


def test_respondent_never_sees_the_recommendations(client, session):
    """응답자 화면에 선물 목록이 나오면 서프라이즈가 깨진다."""
    question_ids, answers = make_answers()
    survey = Survey(
        recipient_name="민지",
        relation="연인",
        occasion="기념일",
        budget_min=BUDGET[0],
        budget_max=BUDGET[1],
        question_ids=question_ids,
    )
    session.add(survey)
    session.commit()
    assert client.post(f"/g/{survey.slug}/submit", json={"answers": answers}).status_code == 200

    pages = [client.get(f"/g/{survey.slug}").text, client.get(f"/g/{survey.slug}/result").text]
    for html in pages:
        assert "shopping.naver" not in html
        assert "쇼핑 검색" not in html
        assert "이건 피하세요" not in html
        assert "radar" not in html
        assert not [name for name in GIFT_NAMES if name in html]


def test_whole_journey_from_form_to_recommendations(client, session):
    form = client.get("/new").text
    created = client.post(
        "/new",
        data={
            "csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', form).group(1),
            "recipient_name": "민지",
            "relation": "친구",
            "occasion": "생일",
            "budget_min": "50000",
            "budget_max": "100000",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    links_path = created.headers["location"]
    result_path = links_path.removesuffix("/links")
    links = client.get(links_path).text
    slug = re.search(r"https://test\.example/g/([A-Za-z0-9]+)", links).group(1)
    assert "아직 응답 전이에요" in client.get(result_path).text

    questions = json.loads(re.search(r'id="game-data">(.*?)</script>', client.get(f"/g/{slug}").text, re.S).group(1))
    answers = [{"question_id": question["id"], "choice": 0} for question in questions]
    assert client.post(f"/g/{slug}/submit", json={"answers": answers}).status_code == 200

    html = client.get(result_path).text
    assert len(card_names(html)) == RECOMMENDATION_COUNT
    assert "5만~10만 원" in html
    assert build_report(answers, 50_000, 100_000)["recommendations"][0]["name"] == card_names(html)[0]


@pytest.mark.parametrize(
    ("low", "high", "text"),
    [(30_000, 70_000, "3만~7만 원"), (25_000, 60_000, "2.5만~6만 원"), (100_000, 300_000, "10만~30만 원")],
)
def test_format_won_range(low, high, text):
    assert format_won_range(low, high) == text
