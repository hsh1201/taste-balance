import json
import random
import re
import threading
from html import unescape
from pathlib import Path

import anthropic
import httpx2
import pytest
from sqlmodel import Session, select

from app.config import settings
from app.models import AiComment, Response, Survey
from app.security import ai_comment_limiter
from app.services import ai_comment
from app.services.ai_comment import FALLBACK_BETA, PICK_COUNT, build_prompt_payload
from app.services.persona import determine_persona, load_personas, persona_of
from app.services.questions import load_questions, pick_question_ids
from app.services.recommend import RECOMMENDATION_COUNT, build_report

BUDGET = (30_000, 70_000)
STATIC_DIR = Path(__file__).resolve().parent.parent / "app" / "static"
GOOD_COMMENT = "요즘 이런 분위기를 좋아하시는 민지님께 잘 맞아요. 부담 없이 건네기에도 딱이에요!"
SECRET_KEY_FOR_TEST = "test-llm-key"


def make_answers(seed: int = 1) -> tuple[list[str], list[dict]]:
    rng = random.Random(seed)
    question_ids = pick_question_ids(rng)
    return question_ids, [{"question_id": qid, "choice": rng.randint(0, 1)} for qid in question_ids]


def add_survey(session: Session, answered: bool = True, seed: int = 1, **overrides) -> Survey:
    question_ids, answers = make_answers(seed)
    values = {
        "recipient_name": "민지",
        "relation": "동료",
        "occasion": "기념일",
        "budget_min": BUDGET[0],
        "budget_max": BUDGET[1],
        "question_ids": question_ids,
    }
    survey = Survey(**(values | overrides))
    session.add(survey)
    session.commit()
    session.refresh(survey)
    if answered:
        session.add(Response(survey_id=survey.id, answers=answers, persona_type=determine_persona(answers)))
        session.commit()
    return survey


def message_body(text: str, stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


def picks_json(numbers=(1, 3, 5), comment=GOOD_COMMENT) -> str:
    return json.dumps({"picks": [{"candidate_number": number, "comment": comment} for number in numbers]}, ensure_ascii=False)


class FakeLlm:
    """실제 SDK를 거치되 네트워크만 가짜로 바꾼다. 요청 본문과 응답 해석까지 검증하기 위해서다."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.respond = lambda _request: httpx2.Response(200, json=message_body(picks_json()))

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.respond(request)

    @property
    def calls(self) -> int:
        return len(self.requests)

    @property
    def body(self) -> dict:
        return json.loads(self.requests[-1].content)

    @property
    def payload(self) -> dict:
        return json.loads(self.body["messages"][0]["content"])


@pytest.fixture
def llm(monkeypatch) -> FakeLlm:
    fake = FakeLlm()
    client = anthropic.Anthropic(
        api_key=SECRET_KEY_FOR_TEST,
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(fake)),
    )
    monkeypatch.setattr(settings, "llm_api_key", SECRET_KEY_FOR_TEST)
    monkeypatch.setattr(ai_comment, "_client", lambda: client)
    return fake


@pytest.fixture
def no_key(monkeypatch) -> list:
    """키가 없을 때 클라이언트를 만들려고 하면 바로 드러나게 한다."""
    calls = []
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(ai_comment, "_client", lambda: calls.append(1))
    return calls


def result_page(client, survey: Survey) -> str:
    response = client.get(f"/r/{survey.admin_token}")
    assert response.status_code == 200
    return response.text


def request_comment(client, survey: Survey):
    return client.post(f"/r/{survey.admin_token}/ai-comment")


def gift_card_count(html: str) -> int:
    return html.count('class="gift-card"')


def stored_comments(session: Session) -> list[AiComment]:
    session.expire_all()
    return session.exec(select(AiComment)).all()


# ---------- 키가 없을 때 ----------


def test_without_key_the_section_is_absent_and_the_rest_works(client, session, no_key):
    survey = add_survey(session)
    html = result_page(client, survey)

    assert "AI 선물 코멘트" not in html
    assert "ai-section" not in html
    assert "ai-comment" not in html
    assert gift_card_count(html) == RECOMMENDATION_COUNT
    assert no_key == []


def test_without_key_the_endpoint_does_not_call_the_llm(client, session, no_key):
    survey = add_survey(session)
    assert request_comment(client, survey).status_code == 204
    assert no_key == []


def test_removing_the_key_hides_a_saved_comment_too(client, session, llm, monkeypatch):
    survey = add_survey(session)
    request_comment(client, survey)
    assert "AI 선물 코멘트" in result_page(client, survey)

    monkeypatch.setattr(settings, "llm_api_key", "")
    assert "AI 선물 코멘트" not in result_page(client, survey)


def test_client_uses_only_the_configured_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "key-from-dotenv")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-from-elsewhere")
    ai_comment._client.cache_clear()
    try:
        assert ai_comment._client().api_key == "key-from-dotenv"
    finally:
        ai_comment._client.cache_clear()


# ---------- 페이지 흐름 ----------


def test_opening_the_page_never_waits_for_the_llm(client, session, llm):
    survey = add_survey(session)
    html = result_page(client, survey)

    assert llm.calls == 0
    assert f'data-ai-comment-url="/r/{survey.admin_token}/ai-comment" hidden' in html
    assert "/static/js/ai-comment.js" in html
    assert gift_card_count(html) == RECOMMENDATION_COUNT


def test_endpoint_returns_three_comments_for_the_chosen_gifts(client, session, llm):
    survey = add_survey(session)
    response = request_comment(client, survey)
    assert response.status_code == 200

    text = unescape(response.text)
    cards = build_report(make_answers()[1], *BUDGET)["recommendations"]
    assert "AI 선물 코멘트" in text
    assert text.count('class="ai-card"') == PICK_COUNT
    assert text.count(GOOD_COMMENT) == PICK_COUNT
    for number in (1, 3, 5):
        assert cards[number - 1]["name"] in text
        assert cards[number - 1]["emoji"] in text
    assert "실제와 다를 수 있어요" in text


def test_comment_is_saved_with_the_model_name(client, session, llm):
    survey = add_survey(session)
    request_comment(client, survey)

    saved = stored_comments(session)
    assert len(saved) == 1
    assert saved[0].survey_id == survey.id
    assert saved[0].model == settings.llm_model
    assert [pick["comment"] for pick in saved[0].picks] == [GOOD_COMMENT] * PICK_COUNT


def test_reloading_does_not_call_the_llm_again(client, session, llm):
    survey = add_survey(session)
    request_comment(client, survey)
    assert llm.calls == 1

    for _ in range(3):
        html = unescape(result_page(client, survey))
        assert html.count(GOOD_COMMENT) == PICK_COUNT
        assert "ai-comment.js" not in html
        assert "data-ai-comment-url" not in html
    assert llm.calls == 1


def test_repeated_requests_use_the_cache(client, session, llm):
    survey = add_survey(session)
    for _ in range(3):
        assert request_comment(client, survey).status_code == 200
    assert llm.calls == 1


def test_each_survey_gets_its_own_comment(client, session, llm):
    first, second = add_survey(session), add_survey(session, seed=2)
    request_comment(client, first)
    request_comment(client, second)
    assert llm.calls == 2
    assert len(stored_comments(session)) == 2


def test_simultaneous_requests_call_the_llm_only_once(engine, session, llm):
    """응답을 기다리는 중에 새로고침해도 호출이 두 번 나가면 안 된다."""
    survey = add_survey(session)
    survey_id = survey.id
    _ids, answers = make_answers()
    report = build_report(answers, *BUDGET)
    persona = load_personas()[determine_persona(answers)]
    entered = threading.Event()
    release = threading.Event()
    good = llm.respond

    def slow(request):
        entered.set()
        assert release.wait(5)
        return good(request)

    llm.respond = slow
    results = []

    def worker():
        with Session(engine) as thread_session:
            results.append(ai_comment.get_or_create_picks(thread_session, survey_id, "민지", persona, report, answers))

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    assert entered.wait(5)
    release.set()
    for thread in threads:
        thread.join(5)

    assert llm.calls == 1
    assert len(results) == 3
    assert all(result and len(result) == PICK_COUNT for result in results)


def test_cache_is_ignored_when_a_gift_left_the_recommendations(client, session, llm):
    survey = add_survey(session)
    request_comment(client, survey)
    saved = stored_comments(session)[0]
    saved.picks = [{"name": "카탈로그에서 사라진 선물", "comment": GOOD_COMMENT}] * PICK_COUNT
    session.add(saved)
    session.commit()

    assert "data-ai-comment-url" in result_page(client, survey)
    assert request_comment(client, survey).status_code == 200
    assert llm.calls == 2
    saved = stored_comments(session)
    assert len(saved) == 1
    assert "카탈로그에서 사라진 선물" not in [pick["name"] for pick in saved[0].picks]


# ---------- 실패 ----------


def status_error(status: int):
    return lambda _request: httpx2.Response(status, json={"type": "error", "error": {"type": "x", "message": "x"}})


def raises(error):
    def respond(request):
        raise error("실패", request=request)

    return respond


def reply(text: str, stop_reason: str = "end_turn"):
    return lambda _request: httpx2.Response(200, json=message_body(text, stop_reason))


FAILURES = {
    "키가 잘못됨": status_error(401),
    "권한 없음": status_error(403),
    "잘못된 요청": status_error(400),
    "요청 한도": status_error(429),
    "서버 오류": status_error(500),
    "과부하": status_error(529),
    "연결 실패": raises(httpx2.ConnectError),
    "시간 초과": raises(httpx2.ReadTimeout),
    "거절": reply("", "refusal"),
    "길이 초과로 잘림": reply('{"picks": [{"candidate_number": 1', "max_tokens"),
    "JSON 아님": reply("죄송해요"),
    "스키마 다름": reply('{"answer": "좋아요"}'),
    "2개만 고름": reply(picks_json(numbers=(1, 2))),
    "4개 고름": reply(picks_json(numbers=(1, 2, 3, 4))),
    "같은 선물 중복": reply(picks_json(numbers=(1, 1, 2))),
    "없는 번호": reply(picks_json(numbers=(1, 2, 9))),
    "0번": reply(picks_json(numbers=(0, 1, 2))),
    "1문장": reply(picks_json(comment="잘 어울려요.")),
    "3문장": reply(picks_json(comment="잘 어울려요. 정말이에요. 추천해요.")),
    "빈 코멘트": reply(picks_json(comment="")),
    "너무 긴 코멘트": reply(picks_json(comment="가" * 400 + ". 좋아요.")),
}


@pytest.mark.parametrize("failure", FAILURES.values(), ids=FAILURES.keys())
def test_failure_hides_the_section_and_saves_nothing(client, session, llm, failure):
    survey = add_survey(session)
    llm.respond = failure

    assert request_comment(client, survey).status_code == 204
    assert stored_comments(session) == []

    html = result_page(client, survey)
    assert gift_card_count(html) == RECOMMENDATION_COUNT
    assert "이건 피하세요" in html
    assert "radar-data" in html


def test_page_stays_up_while_the_llm_is_down(client, session, llm):
    survey = add_survey(session)
    llm.respond = status_error(500)
    request_comment(client, survey)
    assert result_page(client, survey)
    assert client.get(f"/g/{survey.slug}").status_code == 200


def test_failure_is_not_retried_on_every_reload(client, session, llm):
    survey = add_survey(session)
    llm.respond = status_error(500)
    for _ in range(3):
        assert request_comment(client, survey).status_code == 204
    assert llm.calls == 1


def test_failure_is_retried_after_the_cooldown(client, session, llm, monkeypatch):
    survey = add_survey(session)
    good = llm.respond
    llm.respond = status_error(500)
    now = [1000.0]
    monkeypatch.setattr(ai_comment.time, "monotonic", lambda: now[0])
    assert request_comment(client, survey).status_code == 204

    llm.respond = good
    now[0] += ai_comment.FAILURE_COOLDOWN_SECONDS - 1
    assert request_comment(client, survey).status_code == 204
    assert llm.calls == 1

    now[0] += 2
    assert request_comment(client, survey).status_code == 200
    assert llm.calls == 2
    assert len(stored_comments(session)) == 1


def test_failure_of_one_survey_does_not_block_another(client, session, llm):
    first, second = add_survey(session), add_survey(session, seed=2)
    good = llm.respond
    llm.respond = status_error(500)
    assert request_comment(client, first).status_code == 204
    llm.respond = good
    assert request_comment(client, second).status_code == 200


def test_failure_log_never_contains_the_prompt_or_the_key(client, session, llm, caplog):
    survey = add_survey(session, recipient_name="비밀별명")
    llm.respond = status_error(500)
    with caplog.at_level("INFO"):
        request_comment(client, survey)
    assert "AI 코멘트" in caplog.text
    assert "비밀별명" not in caplog.text
    assert SECRET_KEY_FOR_TEST not in caplog.text


def test_endpoint_is_rate_limited_without_touching_the_page(client, session, llm, monkeypatch):
    survey = add_survey(session)
    monkeypatch.setattr(ai_comment_limiter, "limit", 1)
    assert request_comment(client, survey).status_code == 200
    assert request_comment(client, survey).status_code == 429
    assert gift_card_count(result_page(client, survey)) == RECOMMENDATION_COUNT


# ---------- 프롬프트의 개인정보 ----------


def test_prompt_has_only_the_expected_fields(client, session, llm):
    add = add_survey(session)
    request_comment(client, add)
    assert set(llm.payload) == {"받는 사람 별명", "취향 유형", "키워드", "취향 축", "고른 선택지", "후보"}
    assert set(llm.payload["후보"][0]) == {"번호", "선물", "분야", "가격대", "규칙 기반 근거"}
    assert set(llm.payload["취향 유형"]) == {"이름", "설명"}


def test_prompt_contains_the_name_but_no_other_personal_data(client, session, llm):
    survey = add_survey(session, recipient_name="민지", relation="동료", occasion="기념일", budget_min=30_000, budget_max=70_000)
    request_comment(client, survey)

    sent = json.dumps(llm.body, ensure_ascii=False) + "".join(
        f"{key}:{value}" for key, value in llm.requests[-1].headers.items()
    )
    assert llm.payload["받는 사람 별명"] == "민지"
    created_on = survey.created_at.date().isoformat()
    for private in (survey.admin_token, survey.slug, "동료", "기념일", "30000", "70000", "3만~7만", "예산", created_on):
        assert private not in sent, private
    assert "metadata" not in llm.body


def test_prompt_leaves_out_fun_question_answers(client, session, llm):
    survey = add_survey(session)
    request_comment(client, survey)
    sent = json.dumps(llm.payload, ensure_ascii=False)

    questions = load_questions()
    fun_labels = [c["label"] for q in questions.values() if q["kind"] == "fun" for c in q["choices"]]
    assert survey.question_ids
    assert not [label for label in fun_labels if label in sent]


def test_prompt_summarizes_the_signal_answers_and_all_eight_candidates(client, session, llm):
    survey = add_survey(session)
    request_comment(client, survey)
    _ids, answers = make_answers()
    report = build_report(answers, *BUDGET)

    assert [item["선물"] for item in llm.payload["후보"]] == [card["name"] for card in report["recommendations"]]
    assert [item["번호"] for item in llm.payload["후보"]] == list(range(1, RECOMMENDATION_COUNT + 1))
    assert len(llm.payload["고른 선택지"]) == sum(load_questions()[a["question_id"]]["kind"] == "signal" for a in answers)
    assert llm.payload["키워드"] == report["keywords"]
    assert len(llm.payload["취향 축"]) == 6


def test_payload_matches_what_the_route_sends(client, session, llm):
    survey = add_survey(session)
    request_comment(client, survey)
    _ids, answers = make_answers()
    stored = session.exec(select(Response).where(Response.survey_id == survey.id)).one()
    expected = build_prompt_payload("민지", persona_of(stored.persona_type, answers), build_report(answers, *BUDGET), answers)
    assert llm.payload == expected


# ---------- API 요청 형태 ----------


def test_request_asks_for_structured_output_with_the_configured_model(client, session, llm):
    survey = add_survey(session)
    request_comment(client, survey)
    body = llm.body

    assert body["model"] == "claude-haiku-5-5"
    assert body["max_tokens"] == ai_comment.MAX_OUTPUT_TOKENS
    assert body["output_config"]["effort"] == "high"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "picks" in body["output_config"]["format"]["schema"]["properties"]
    assert "정확히 2문장" in body["system"]
    assert "지어내지" in body["system"]
    assert SECRET_KEY_FOR_TEST in llm.requests[-1].headers["authorization"]
    # 최신 모델에서 400을 내는 매개변수를 보내지 않는다.
    assert not {"temperature", "top_p", "top_k", "thinking"} & set(body)


def test_supported_model_opts_into_refusal_fallback(client, session, llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_model", "claude-opus-5-5")
    survey = add_survey(session)
    request_comment(client, survey)
    assert llm.body["model"] == "claude-opus-5-5"
    assert llm.body["fallbacks"] == "default"
    assert FALLBACK_BETA in llm.requests[-1].headers["anthropic-beta"]
    assert stored_comments(session)[0].model == "claude-opus-5-5"


def test_default_model_does_not_send_an_unsupported_fallback(client, session, llm):
    """Haiku 5.5에는 서버 쪽 대체 호출이 없어 보내면 안 된다."""
    survey = add_survey(session)
    assert request_comment(client, survey).status_code == 200

    assert llm.body["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in llm.body
    assert FALLBACK_BETA not in llm.requests[-1].headers.get("anthropic-beta", "")
    assert stored_comments(session)[0].model == "claude-haiku-5-5"


def test_response_that_starts_with_a_thinking_block_is_read_correctly(client, session, llm):
    """Haiku 5.5는 생각 기능이 기본으로 켜져 있어 응답이 thinking 블록으로 시작할 수 있다."""
    survey = add_survey(session)
    body = message_body(picks_json())
    body["content"].insert(0, {"type": "thinking", "thinking": "", "signature": "sig"})
    llm.respond = lambda _request: httpx2.Response(200, json=body)

    response = request_comment(client, survey)
    assert response.status_code == 200
    assert unescape(response.text).count(GOOD_COMMENT) == PICK_COUNT


# ---------- 안전한 표시 ----------


def test_llm_output_is_escaped(client, session, llm):
    survey = add_survey(session)
    llm.respond = reply(picks_json(comment="<script>alert(1)</script> 좋아요. 정말 좋아요."))
    response = request_comment(client, survey)

    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "<script>" not in result_page(client, survey).split('id="radar-data"')[0].replace(
        '<script src="', ""
    ).replace('<script type="application/json"', "")


@pytest.mark.parametrize("state", ["대기 중", "저장됨"])
def test_page_obeys_the_content_security_policy(client, session, llm, state):
    survey = add_survey(session)
    if state == "저장됨":
        request_comment(client, survey)
    html = result_page(client, survey)

    assert not re.search(r"<[^>]+\sstyle=", html)
    assert not re.search(r"<[^>]+\son[a-z]+=", html)
    for attributes in re.findall(r"<script([^>]*)>", html):
        source = re.search(r'src="([^"]+)"', attributes)
        if source:
            assert source.group(1).startswith(("/static/", "https://cdn.jsdelivr.net/"))
        else:
            assert 'type="application/json"' in attributes


def test_script_and_page_agree_on_the_attribute_name():
    script = (STATIC_DIR / "js" / "ai-comment.js").read_text(encoding="utf-8")
    assert "[data-ai-comment-url]" in script
    assert "dataset.aiCommentUrl" in script


def test_script_is_served(client):
    assert client.get("/static/js/ai-comment.js").status_code == 200


# ---------- 접근 ----------


def test_unknown_token_is_rejected_without_calling_the_llm(client, llm):
    assert client.post("/r/unknown-token/ai-comment").status_code == 404
    assert llm.calls == 0


def test_share_slug_cannot_request_a_comment(client, session, llm):
    survey = add_survey(session)
    assert client.post(f"/r/{survey.slug}/ai-comment").status_code == 404
    assert llm.calls == 0


def test_expired_survey_is_rejected_without_calling_the_llm(client, session, llm):
    survey = add_survey(session)
    survey.expires_at = survey.created_at
    session.add(survey)
    session.commit()
    assert request_comment(client, survey).status_code == 410
    assert llm.calls == 0


def test_survey_without_a_response_does_not_call_the_llm(client, session, llm):
    survey = add_survey(session, answered=False)
    assert request_comment(client, survey).status_code == 204
    assert llm.calls == 0
    assert "아직 응답 전이에요" in result_page(client, survey)


def test_endpoint_does_not_answer_get(client, session, llm):
    """링크 미리보기 봇이 GET으로 열어도 비용이 생기지 않아야 한다."""
    survey = add_survey(session)
    assert client.get(f"/r/{survey.admin_token}/ai-comment").status_code == 405
    assert llm.calls == 0


def test_endpoint_response_is_private(client, session, llm):
    survey = add_survey(session)
    response = request_comment(client, survey)
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert response.headers["cache-control"] == "no-store"


def test_respondent_never_sees_or_triggers_the_comment(client, session, llm):
    question_ids, answers = make_answers()
    survey = add_survey(session, answered=False)
    survey.question_ids = question_ids
    session.add(survey)
    session.commit()
    assert client.post(f"/g/{survey.slug}/submit", json={"answers": answers}).status_code == 200

    pages = [client.get(f"/g/{survey.slug}").text, client.get(f"/g/{survey.slug}/result").text]
    for html in pages:
        assert "AI 선물 코멘트" not in html
        assert "ai-comment" not in html
    assert llm.calls == 0
