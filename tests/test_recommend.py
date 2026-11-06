import random
import re
import sys
from collections import Counter
from itertools import combinations
from urllib.parse import parse_qs, urlparse

import pytest

from app.models import BUDGET_MAX, BUDGET_MIN
from app.services.questions import load_questions, pick_question_ids
from app.services.recommend import (
    FALLBACK_AVOID_REASON,
    FALLBACK_EVIDENCE,
    MAX_PER_CATEGORY,
    RECOMMENDATION_COUNT,
    SECOND_AVOID_MAX_GAP,
    build_report,
    category_scores,
    contrast_sentence,
    evidence_sentence,
    load_gifts,
    lowest_categories,
    overlaps_budget,
    pick_diverse,
    rank_gifts,
    score_gift,
    signal_contrasts,
    taste_keywords,
)
from app.services.tags import ALL_TAGS, AXES, AXIS_TAGS, CATEGORIES, POLE_LABELS, SUBTAG_LABELS, SUBTAGS, category_of
from app.services.taste import taste_vector
from scripts.preview_recommend import main as preview_main

GIFTS = load_gifts()
GIFTS_BY_NAME = {gift["name"]: gift for gift in GIFTS}
QUESTIONS = load_questions()

BUDGET_STEP = 10_000
GIFT_FIELDS = {"name", "emoji", "category", "price_min", "price_max", "tags", "reason_template"}
# 선물마다 축 태그 합을 같게 맞춰, 태그가 많다는 이유만으로 점수가 오르지 않게 한다.
AXIS_WEIGHT_TOTAL = 4
TYPICAL_BUDGETS = [(10_000, 30_000), (30_000, 70_000), (50_000, 100_000), (100_000, 200_000), (200_000, 300_000)]
NARROW_BUDGETS = [(low, low + BUDGET_STEP) for low in range(BUDGET_MIN, BUDGET_MAX, BUDGET_STEP)]
# 싼 물건일수록 가격 폭이 좁아 같은 폭으로 나누면 구간마다 후보 수가 크게 달라진다.
PRICE_TIERS = [
    (10_000, 20_000),
    (20_000, 30_000),
    (30_000, 40_000),
    (40_000, 60_000),
    (60_000, 90_000),
    (90_000, 130_000),
    (130_000, 180_000),
    (180_000, 230_000),
    (230_000, 290_000),
    (290_000, 400_000),
]
CATEGORY_TAGS = {
    category: (category, *(tag for tag, parent in SUBTAGS.items() if parent == category)) for category in CATEGORIES
}
QUOTE = re.compile(r"'([^']+)'보다 '([^']+)'[을를]")


def answer(question_id: str, choice: int) -> dict:
    return {"question_id": question_id, "choice": choice}


def random_answers(seed: int) -> list[dict]:
    rng = random.Random(seed)
    return [answer(question_id, rng.randint(0, 1)) for question_id in pick_question_ids(rng)]


def answers_favoring(tags: tuple[str, ...], seed: int) -> list[dict]:
    rng = random.Random(seed)
    answers = []
    for question_id in pick_question_ids(rng):
        scores = [sum(choice["tags"].get(tag, 0) for tag in tags) for choice in QUESTIONS[question_id]["choices"]]
        pick = rng.randint(0, 1) if scores[0] == scores[1] else scores.index(max(scores))
        answers.append(answer(question_id, pick))
    return answers


def make_gift(name: str, category: str = "food", **tags: int) -> dict:
    return {
        "name": name,
        "category": category,
        "price_min": 20_000,
        "price_max": 40_000,
        "tags": tags,
        "score": 0,
    }


def contrasts_of(answers: list[dict]) -> dict[tuple[str, str], tuple[dict, dict, dict]]:
    """인용될 수 있는 (고르지 않은 라벨, 고른 라벨) -> (문항, 고른 선택지, 고르지 않은 선택지)."""
    contrasts = {}
    for item in answers:
        question = QUESTIONS[item["question_id"]]
        chosen = question["choices"][item["choice"]]
        rejected = question["choices"][1 - item["choice"]]
        contrasts[(rejected["label"], chosen["label"])] = (question, chosen, rejected)
    return contrasts


def dot(weights: dict, tags: dict) -> int:
    return sum(weight * tags.get(tag, 0) for tag, weight in weights.items())


def category_weight(tags: dict, category: str) -> int:
    return sum(weight for tag, weight in tags.items() if category_of(tag) == category)


# ---------- 카탈로그 ----------


def test_catalog_has_120_gifts_with_unique_names():
    assert len(GIFTS) == 120
    assert len(GIFTS_BY_NAME) == 120


def test_catalog_covers_every_category():
    assert set(Counter(gift["category"] for gift in GIFTS)) == set(CATEGORIES)


@pytest.mark.parametrize("gift", GIFTS, ids=lambda gift: gift["name"])
def test_gift_shape(gift):
    assert set(gift) == GIFT_FIELDS
    assert gift["emoji"]
    assert gift["category"] in CATEGORIES
    assert 0 < gift["price_min"] < gift["price_max"]
    assert set(gift["tags"]) <= ALL_TAGS
    assert all(isinstance(weight, int) and weight > 0 for weight in gift["tags"].values())


@pytest.mark.parametrize("gift", GIFTS, ids=lambda gift: gift["name"])
def test_gift_carries_its_own_category_tag(gift):
    """상위 태그가 없으면 '은은하게 퍼진 좋은 향' 같은 분야 단위 선택이 점수에 반영되지 않는다."""
    assert gift["category"] in gift["tags"]


@pytest.mark.parametrize("gift", GIFTS, ids=lambda gift: gift["name"])
def test_gift_axis_weights_add_up_to_the_same_total(gift):
    assert sum(weight for tag, weight in gift["tags"].items() if tag in AXIS_TAGS) == AXIS_WEIGHT_TOTAL


@pytest.mark.parametrize("gift", GIFTS, ids=lambda gift: gift["name"])
def test_reason_template_appends_a_sentence_to_the_evidence(gift):
    template = gift["reason_template"]
    assert template.startswith("{evidence} ")
    assert template.count("{") == template.count("}") == 1
    assert template.endswith(".")


def test_catalog_uses_every_axis_pole_and_subtag():
    used = {tag for gift in GIFTS for tag in gift["tags"]}
    assert AXIS_TAGS | SUBTAGS.keys() <= used


@pytest.mark.parametrize("budget", NARROW_BUDGETS, ids=str)
def test_every_budget_can_fill_all_cards_even_in_the_worst_case(budget):
    """가장 좁은 예산에서 어떤 두 분야를 피하라고 하더라도 카드 8개가 차야 한다."""
    counts = Counter(gift["category"] for gift in GIFTS if overlaps_budget(gift, *budget))
    assert all(counts[category] >= 1 for category in CATEGORIES)
    for avoided in combinations(CATEGORIES, 2):
        cards = sum(min(MAX_PER_CATEGORY, counts[category]) for category in CATEGORIES if category not in avoided)
        assert cards >= RECOMMENDATION_COUNT, (budget, avoided)


def test_price_tiers_hold_about_the_same_number_of_gifts():
    counts = [sum(low <= gift["price_min"] < high for gift in GIFTS) for low, high in PRICE_TIERS]
    assert sum(counts) == len(GIFTS)
    assert max(counts) - min(counts) <= 2


def test_every_budget_offers_a_similar_number_of_candidates():
    counts = [sum(overlaps_budget(gift, *budget) for gift in GIFTS) for budget in NARROW_BUDGETS]
    assert min(counts) >= 15
    assert max(counts) <= 3 * min(counts)


@pytest.mark.parametrize("gift", GIFTS, ids=lambda gift: gift["name"])
def test_price_stays_between_ten_thousand_and_the_three_hundred_thousands(gift):
    assert BUDGET_MIN <= gift["price_min"] <= BUDGET_MAX
    assert gift["price_max"] < 400_000


@pytest.mark.parametrize("gift", GIFTS, ids=lambda gift: gift["name"])
def test_gift_can_be_bought_through_the_shopping_link(gift):
    """전통주가 아닌 술은 온라인으로 팔 수 없어 쇼핑 검색으로 살 수 없다."""
    if "클래스" not in gift["name"]:
        assert not any(word in gift["name"] for word in ("와인", "위스키", "샴페인", "맥주", "보드카", "하이볼"))


# ---------- 점수 계산 ----------


def test_score_is_the_dot_product_of_gift_tags_and_taste_vector():
    gift = make_gift("캔들", candle=4, scent=3, emotional=2)
    vector = {"candle": 2, "emotional": 3, "tech": 5}
    assert score_gift(gift, vector) == 4 * 2 + 3 * 0 + 2 * 3


def test_score_is_zero_when_no_tags_overlap():
    assert score_gift(make_gift("캔들", candle=4), {"tech": 5}) == 0


def test_score_uses_the_tags_of_the_chosen_side_only():
    gift = make_gift("랜턴", camping=3, home=2)
    assert score_gift(gift, taste_vector([answer("q01", 0)])) == 3 * 2
    assert score_gift(gift, taste_vector([answer("q01", 1)])) == 2 * 1


def test_gifts_are_ranked_by_score_then_name():
    gifts = [make_gift("나", candle=1), make_gift("다", candle=3), make_gift("가", candle=1)]
    assert [gift["name"] for gift in rank_gifts(gifts, {"candle": 1})] == ["다", "가", "나"]


def test_ranking_does_not_change_the_catalog():
    before = [dict(gift) for gift in GIFTS]
    rank_gifts(GIFTS, {"candle": 2})
    assert GIFTS == before


# ---------- 예산 필터 ----------


@pytest.mark.parametrize(
    ("budget_min", "budget_max", "expected"),
    [
        (25_000, 30_000, True),  # 가격대 안쪽
        (10_000, 100_000, True),  # 가격대를 포함
        (10_000, 20_000, True),  # 최저가에서 맞닿음
        (40_000, 50_000, True),  # 최고가에서 맞닿음
        (10_000, 19_999, False),
        (40_001, 50_000, False),
    ],
)
def test_budget_overlap_boundaries(budget_min, budget_max, expected):
    assert overlaps_budget(make_gift("선물"), budget_min, budget_max) is expected


# ---------- 카테고리 다양성 ----------


def test_diversity_caps_each_category_at_two():
    ranked = [make_gift(f"food{i}", "food") for i in range(5)] + [make_gift(f"tech{i}", "tech") for i in range(5)]
    picked = pick_diverse(ranked)
    assert [gift["name"] for gift in picked] == ["food0", "food1", "tech0", "tech1"]


def test_diversity_skips_to_the_next_category_while_keeping_score_order():
    categories = ["food", "food", "food", "tech", "food", "tech", "tech", "scent"]
    ranked = [make_gift(f"{index}", category) for index, category in enumerate(categories)]
    assert [gift["name"] for gift in pick_diverse(ranked)] == ["0", "1", "3", "5", "7"]


def test_diversity_stops_at_eight():
    ranked = [make_gift(f"{category}{i}", category) for category in CATEGORIES for i in range(3)]
    picked = pick_diverse(ranked)
    assert len(picked) == RECOMMENDATION_COUNT
    assert max(Counter(gift["category"] for gift in picked).values()) == MAX_PER_CATEGORY


def test_diversity_returns_what_it_can_when_candidates_run_out():
    assert [gift["name"] for gift in pick_diverse([make_gift("하나")])] == ["하나"]
    assert pick_diverse([]) == []


# ---------- 근거 문장 ----------


def test_contrast_sentence_matches_the_spec_example():
    pairs = [({"label": "캠핑"}, {"label": "호캉스"}), ({"label": "캔들"}, {"label": "향수"})]
    assert contrast_sentence(pairs) == "'호캉스'보다 '캠핑'을, '향수'보다 '캔들'을 골랐어요."


@pytest.mark.parametrize(
    ("chosen", "rejected", "expected"),
    [
        ("캠핑", "호캉스", "'호캉스'보다 '캠핑'을 골랐어요."),
        ("치킨과 맥주", "마스크팩과 반신욕", "'마스크팩과 반신욕'보다 '치킨과 맥주'를 골랐어요."),
        ("풀메이크업", "선크림만 슥", "'선크림만 슥'보다 '풀메이크업'을 골랐어요."),
        ("혼자 조용히 영화", "모여서 보드게임", "'모여서 보드게임'보다 '혼자 조용히 영화'를 골랐어요."),
    ],
)
def test_contrast_sentence_picks_the_right_particle(chosen, rejected, expected):
    assert contrast_sentence([({"label": chosen}, {"label": rejected})]) == expected


def test_every_choice_label_ends_in_hangul_so_the_particle_is_correct():
    for question in QUESTIONS.values():
        for choice in question["choices"]:
            assert "가" <= choice["label"][-1] <= "힣", choice["label"]


def test_recommendation_quotes_the_choice_that_raised_its_score():
    report = build_report([answer("q02", 1), answer("q01", 0)], 20_000, 40_000)
    cards = {card["name"]: card for card in report["recommendations"]}
    assert "'내 몸에서 나는 향수'보다 '내 방에 퍼지는 캔들'을" in cards["소이 캔들"]["reason"]
    assert "'호캉스'보다 '캠핑'을" in cards["캠핑 랜턴"]["reason"]


def test_reason_ends_with_the_sentence_from_the_template():
    report = build_report([answer("q02", 1)], 20_000, 40_000)
    card = next(card for card in report["recommendations"] if card["name"] == "소이 캔들")
    assert card["reason"] == "'내 몸에서 나는 향수'보다 '내 방에 퍼지는 캔들'을 골랐어요. 퇴근 후 불을 켜는 순간 방 안이 포근해져요."


def test_choice_that_argues_against_the_gift_is_not_quoted():
    """캔들 대신 향수를 고른 답은 캔들을 추천하는 근거가 될 수 없다."""
    contrasts = signal_contrasts([answer("q02", 0)])
    assert evidence_sentence(GIFTS_BY_NAME["소이 캔들"], contrasts, Counter()) == FALLBACK_EVIDENCE


def test_weak_second_quote_is_left_out():
    """집콕 선택도 조금 맞지만, 캔들을 추천하는 근거로 붙이기엔 약하다."""
    contrasts = signal_contrasts([answer("q02", 1), answer("q09", 0)])
    sentence = evidence_sentence(GIFTS_BY_NAME["소이 캔들"], contrasts, Counter())
    assert sentence == "'내 몸에서 나는 향수'보다 '내 방에 퍼지는 캔들'을 골랐어요."


def test_strongest_quote_comes_first_even_after_being_quoted_before():
    contrasts = signal_contrasts([answer("q02", 1), answer("q23", 1)])
    gift = GIFTS_BY_NAME["소이 캔들"]
    assert evidence_sentence(gift, contrasts, Counter({"q02": 5})) == (
        "'내 몸에서 나는 향수'보다 '내 방에 퍼지는 캔들'을, '모여서 보드게임'보다 '거절하고 집에서 영화'를 골랐어요."
    )


def test_answer_quoted_many_times_makes_room_for_a_fresh_one():
    contrasts = signal_contrasts([answer("q02", 1), answer("q09", 0)])
    gift = GIFTS_BY_NAME["소이 캔들"]
    assert evidence_sentence(gift, contrasts, Counter({"q02": 10})) == (
        "'내 몸에서 나는 향수'보다 '내 방에 퍼지는 캔들'을, '웨이팅 맛집 도장깨기'보다 '침대에서 정주행'을 골랐어요."
    )


def test_quoting_is_counted_for_the_next_cards():
    contrasts = signal_contrasts([answer("q02", 1), answer("q23", 1)])
    quoted = Counter({"q02": 5})
    evidence_sentence(GIFTS_BY_NAME["소이 캔들"], contrasts, quoted)
    assert quoted == {"q02": 6, "q23": 1}


def test_fun_question_is_never_quoted():
    """재미 문항은 점수에는 들어가지만 근거로 내세우지 않는다."""
    report = build_report([answer("f01", 0)], 10_000, 300_000)
    assert all(card["reason"].startswith(FALLBACK_EVIDENCE) for card in report["recommendations"])


@pytest.mark.parametrize("seed", range(100))
def test_quotes_are_real_answers_that_favor_the_gift(seed):
    answers = random_answers(seed)
    contrasts = contrasts_of(answers)
    for card in build_report(answers, 30_000, 100_000)["recommendations"]:
        quotes = QUOTE.findall(card["reason"])
        assert 1 <= len(quotes) <= 2
        assert FALLBACK_EVIDENCE not in card["reason"]
        for quote in quotes:
            question, chosen, rejected = contrasts[quote]
            assert question["kind"] == "signal"
            gift_tags = GIFTS_BY_NAME[card["name"]]["tags"]
            assert dot(gift_tags, chosen["tags"]) > dot(gift_tags, rejected["tags"])


def test_cards_do_not_all_repeat_the_same_quote():
    for seed in range(100):
        cards = build_report(random_answers(seed), 30_000, 100_000)["recommendations"]
        counts = Counter(quote for card in cards for quote in QUOTE.findall(card["reason"]))
        assert max(counts.values()) < RECOMMENDATION_COUNT
        assert len(counts) >= 3


# ---------- 이건 피하세요 ----------


def test_category_score_averages_the_top_gifts_of_each_category():
    ranked = [
        {**make_gift("a", "food"), "score": 10},
        {**make_gift("b", "tech"), "score": 9},
        {**make_gift("c", "food"), "score": 6},
        {**make_gift("d", "food"), "score": 1},
    ]
    assert category_scores(ranked) == {"food": 8, "tech": 9}


def test_lowest_category_comes_first():
    assert lowest_categories({"food": 20, "tech": 5, "scent": 30}, {})[0] == "tech"


def test_second_category_is_added_only_when_it_is_also_low():
    close = {"tech": 5, "food": 5 + SECOND_AVOID_MAX_GAP, "scent": 30}
    far = {"tech": 5, "food": 5 + SECOND_AVOID_MAX_GAP + 1, "scent": 30}
    assert lowest_categories(close, {}) == ["tech", "food"]
    assert lowest_categories(far, {}) == ["tech"]


def test_category_the_player_actually_chose_is_not_avoided():
    scores = {"tech": 5, "food": 6, "scent": 30}
    assert lowest_categories(scores, {"tech": 2}) == ["food"]
    assert lowest_categories(scores, {"coffee": 2}) == ["tech"]


def test_lowest_score_decides_when_every_category_was_chosen():
    assert lowest_categories({"tech": 5, "food": 30}, {"tech": 2, "food": 2}) == ["tech"]


@pytest.mark.parametrize("seed", range(100))
def test_avoid_reason_quotes_the_answer_that_pushed_the_category_away(seed):
    answers = random_answers(seed)
    contrasts = contrasts_of(answers)
    for item in build_report(answers, 30_000, 100_000)["avoid"]:
        if item["reason"] == FALLBACK_AVOID_REASON:
            continue
        quotes = QUOTE.findall(item["reason"])
        assert quotes
        for quote in quotes:
            question, chosen, rejected = contrasts[quote]
            assert question["kind"] == "signal"
            assert category_weight(rejected["tags"], item["category"]) > category_weight(chosen["tags"], item["category"])


# ---------- 결과 리포트 전체 ----------


@pytest.mark.parametrize("budget", NARROW_BUDGETS, ids=str)
def test_every_narrow_budget_still_fills_eight_cards(budget):
    for seed in range(30):
        assert len(build_report(random_answers(seed), *budget)["recommendations"]) == RECOMMENDATION_COUNT


@pytest.mark.parametrize("budget", [*TYPICAL_BUDGETS, (BUDGET_MIN, BUDGET_MAX)], ids=str)
@pytest.mark.parametrize("seed", range(40))
def test_report_keeps_every_promise(seed, budget):
    answers = random_answers(seed)
    report = build_report(answers, *budget)
    cards = report["recommendations"]

    assert len(cards) == RECOMMENDATION_COUNT
    assert len({card["name"] for card in cards}) == RECOMMENDATION_COUNT
    assert max(Counter(card["category"] for card in cards).values()) <= MAX_PER_CATEGORY
    assert all(overlaps_budget(card, *budget) for card in cards)
    assert [card["score"] for card in cards] == sorted((card["score"] for card in cards), reverse=True)
    assert all("{" not in card["reason"] for card in cards)

    avoided = [item["category"] for item in report["avoid"]]
    assert 1 <= len(avoided) <= 2
    assert not set(avoided) & {card["category"] for card in cards}


def test_recommendations_are_the_top_scores_outside_the_avoided_categories():
    answers = random_answers(1)
    budget = (30_000, 100_000)
    report = build_report(answers, *budget)
    avoided = {item["category"] for item in report["avoid"]}
    vector = taste_vector(answers)
    candidates = [gift for gift in GIFTS if gift["category"] not in avoided and overlaps_budget(gift, *budget)]
    expected = pick_diverse(rank_gifts(candidates, vector))
    assert [card["name"] for card in report["recommendations"]] == [gift["name"] for gift in expected]


def test_report_is_identical_every_time():
    answers = random_answers(3)
    assert build_report(answers, 30_000, 70_000) == build_report(answers, 30_000, 70_000)


def test_shopping_link_searches_naver_for_the_gift_name():
    for card in build_report(random_answers(4), 10_000, 300_000)["recommendations"]:
        url = urlparse(card["shopping_url"])
        assert (url.scheme, url.netloc, url.path) == ("https", "search.shopping.naver.com", "/search/all")
        assert parse_qs(url.query) == {"query": [card["name"]]}


def test_card_has_what_the_result_page_needs():
    card = build_report(random_answers(5), 30_000, 70_000)["recommendations"][0]
    assert CATEGORIES[card["category"]] == card["category_label"]
    assert card["emoji"] and card["reason"]
    assert card["price_min"] < card["price_max"]


def test_radar_has_six_axes_in_order_with_labels():
    radar = build_report([answer("q10", 1), answer("q22", 1)], 30_000, 70_000)["radar"]
    assert [item["axis"] for item in radar] == list(AXES)
    together = next(item for item in radar if item["axis"] == "solo_together")
    assert together == {
        "axis": "solo_together",
        "left": "혼자",
        "right": "함께",
        "value": 100,
        "lean_label": "함께",
        "lean_value": 100,
    }
    assert all(0 <= item["value"] <= 100 for item in radar)


@pytest.mark.parametrize(
    ("answers", "label", "value"),
    [
        ([answer("q10", 0), answer("q22", 0)], "혼자", 100),  # 왼쪽으로 기울어도 값이 작아지지 않는다.
        ([answer("q10", 1), answer("q22", 1)], "함께", 100),
        ([answer("q10", 0), answer("q22", 1)], "혼자·함께", 50),
    ],
)
def test_radar_leans_toward_the_stronger_pole(answers, label, value):
    radar = build_report(answers, 30_000, 70_000)["radar"]
    together = next(item for item in radar if item["axis"] == "solo_together")
    assert (together["lean_label"], together["lean_value"]) == (label, value)


@pytest.mark.parametrize("seed", range(50))
def test_radar_lean_stays_between_middle_and_end(seed):
    for item in build_report(random_answers(seed), 30_000, 70_000)["radar"]:
        assert 50 <= item["lean_value"] <= 100


def test_keywords_show_clear_leanings_and_interests():
    keywords = taste_keywords([answer("q10", 1), answer("q22", 1), answer("q18", 1)])
    assert "함께" in keywords
    assert "혼자" not in keywords
    assert "테크/가젯" in keywords


def test_keywords_prefer_a_subtag_over_its_parent_category():
    keywords = taste_keywords([answer("q01", 0), answer("q26", 1)])
    assert "캠핑" in keywords
    assert "취미" not in keywords


@pytest.mark.parametrize("seed", range(100))
def test_keywords_are_known_labels_without_opposite_poles(seed):
    keywords = taste_keywords(random_answers(seed))
    known = {*POLE_LABELS.values(), *CATEGORIES.values(), *SUBTAG_LABELS.values()}
    assert 1 <= len(keywords) <= 6
    assert len(set(keywords)) == len(keywords)
    assert set(keywords) <= known
    for left, right in AXES.values():
        assert not {POLE_LABELS[left], POLE_LABELS[right]} <= set(keywords)


def test_preview_script_prints_every_section(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["preview_recommend", "30000", "70000", "7"])
    preview_main()
    printed = capsys.readouterr().out
    for section in ("[고른 답]", "[취향 레이더]", "[키워드]", "[추천 8개", "[이건 피하세요]", "search.shopping.naver.com"):
        assert section in printed


def test_report_survives_answers_without_any_known_question():
    report = build_report([answer("removed", 0)], 30_000, 70_000)
    assert len(report["recommendations"]) == RECOMMENDATION_COUNT
    assert all(card["score"] == 0 for card in report["recommendations"])
    assert 1 <= len(report["avoid"]) <= 2


# ---------- 추천 품질 ----------


def test_different_tastes_get_different_gifts():
    home = build_report(answers_favoring(("home", "emotional", "interior"), 0), 30_000, 100_000)
    outdoor = build_report(answers_favoring(("outdoor", "practical", "sports"), 0), 30_000, 100_000)
    shared = {card["name"] for card in home["recommendations"]} & {card["name"] for card in outdoor["recommendations"]}
    assert len(shared) <= 2


@pytest.mark.parametrize("category", CATEGORIES)
def test_people_who_like_a_category_get_it_recommended(category):
    games = 200
    reports = [
        build_report(answers_favoring(CATEGORY_TAGS[category], seed), 30_000, 100_000) for seed in range(games)
    ]
    recommended = sum(any(card["category"] == category for card in r["recommendations"]) for r in reports)
    avoided = sum(any(item["category"] == category for item in r["avoid"]) for r in reports)
    assert recommended / games >= 0.75
    assert avoided / games <= 0.05


def test_random_players_see_every_category_without_one_taking_over():
    games = 1000
    counts = Counter(
        card["category"]
        for seed in range(games)
        for card in build_report(random_answers(seed), 30_000, 100_000)["recommendations"]
    )
    assert set(counts) == set(CATEGORIES)
    per_report = {category: counts[category] / games for category in CATEGORIES}
    assert max(per_report.values()) <= 1.5
    assert min(per_report.values()) >= 0.6


@pytest.mark.parametrize("budget", TYPICAL_BUDGETS, ids=str)
def test_no_gift_is_recommended_to_almost_everyone(budget):
    games = 500
    counts = Counter(
        card["name"] for seed in range(games) for card in build_report(random_answers(seed), *budget)["recommendations"]
    )
    assert counts.most_common(1)[0][1] / games < 0.6


def test_every_gift_can_be_recommended():
    seen = {
        card["name"]
        for budget in TYPICAL_BUDGETS
        for seed in range(500)
        for card in build_report(random_answers(seed), *budget)["recommendations"]
    }
    assert seen == set(GIFTS_BY_NAME)
