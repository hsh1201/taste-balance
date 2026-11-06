import random
from collections import Counter

import pytest

from app.services.questions import _pick_balanced, covered_targets, load_questions, pick_question_ids
from app.services.tags import ALL_TAGS, AXES, AXIS_TAGS, CATEGORIES, SUBTAGS

QUESTIONS = load_questions()
ALL_QUESTIONS = list(QUESTIONS.values())
GAMES = [pick_question_ids(random.Random(seed)) for seed in range(2000)]


def test_pool_has_40_questions_with_30_percent_fun():
    assert len(QUESTIONS) == 40
    assert Counter(q["kind"] for q in ALL_QUESTIONS) == {"signal": 28, "fun": 12}


@pytest.mark.parametrize("question", ALL_QUESTIONS, ids=lambda q: q["id"])
def test_question_shape(question):
    assert question["text"]
    assert len(question["choices"]) == 2
    for choice in question["choices"]:
        assert choice["label"]
        assert choice["emoji"]
        assert set(choice["tags"]) <= ALL_TAGS
        assert all(isinstance(weight, int) and weight > 0 for weight in choice["tags"].values())


@pytest.mark.parametrize("question", ALL_QUESTIONS, ids=lambda q: q["id"])
def test_question_never_reveals_the_purpose(question):
    texts = [question["text"], *(choice["label"] for choice in question["choices"])]
    for text in texts:
        assert "선물" not in text
        assert "받고" not in text
    assert all(choice["emoji"] != "🎁" for choice in question["choices"])


@pytest.mark.parametrize("question", ALL_QUESTIONS, ids=lambda q: q["id"])
def test_same_tag_with_same_weight_on_both_sides_is_not_allowed(question):
    """양쪽에 같은 값이 있으면 선택과 무관하게 점수가 올라 추천이 왜곡된다."""
    first, second = (choice["tags"] for choice in question["choices"])
    shared = {tag for tag in first.keys() & second.keys() if first[tag] == second[tag]}
    assert shared == set()


@pytest.mark.parametrize(
    "question", [q for q in ALL_QUESTIONS if q["kind"] == "fun"], ids=lambda q: q["id"]
)
def test_fun_questions_only_carry_weak_axis_tags(question):
    for choice in question["choices"]:
        assert set(choice["tags"]) <= AXIS_TAGS
        assert all(weight == 1 for weight in choice["tags"].values())


def test_every_subtag_is_used_by_some_question():
    used = {tag for q in ALL_QUESTIONS for choice in q["choices"] for tag in choice["tags"]}
    assert set(SUBTAGS) <= used


@pytest.mark.parametrize(
    "question", [q for q in ALL_QUESTIONS if q["kind"] == "signal"], ids=lambda q: q["id"]
)
def test_both_sides_of_a_signal_question_say_something_about_taste(question):
    assert all(choice["tags"] for choice in question["choices"])


def test_questions_and_choices_are_not_repeated():
    texts = [q["text"] for q in ALL_QUESTIONS]
    labels = [choice["label"] for q in ALL_QUESTIONS for choice in q["choices"]]
    assert len(set(texts)) == len(texts)
    assert len(set(labels)) == len(labels)


def test_text_fits_the_game_screen():
    for question in ALL_QUESTIONS:
        assert len(question["text"]) <= 30, question["text"]
        for choice in question["choices"]:
            assert len(choice["label"]) <= 18, choice["label"]


def test_every_pool_and_category_can_be_chosen_or_turned_down():
    """고를 수만 있고 거절할 수 없는 분야가 있으면 '이건 피하세요' 근거를 만들 수 없다."""
    for category in CATEGORIES:
        counts = sum(category in covered_targets(q) for q in ALL_QUESTIONS if q["kind"] == "signal")
        assert counts >= 3, category


def test_axis_poles_show_up_about_equally_often():
    """특정 극만 자주 나오면 그 태그가 붙은 선물이 누구에게나 유리해진다."""
    poles = Counter()
    for game in GAMES[:1000]:
        for qid in game:
            for choice in QUESTIONS[qid]["choices"]:
                poles.update({tag: weight / 2 for tag, weight in choice["tags"].items() if tag in AXIS_TAGS})
    assert set(poles) == set(AXIS_TAGS)
    assert max(poles.values()) / min(poles.values()) < 2


def test_game_has_15_unique_questions_with_10_signal_and_5_fun():
    for game in GAMES:
        assert len(game) == len(set(game)) == 15
        assert Counter(QUESTIONS[qid]["kind"] for qid in game) == {"signal": 10, "fun": 5}


def test_fun_questions_are_spread_through_the_game():
    for game in GAMES:
        kinds = [QUESTIONS[qid]["kind"] for qid in game]
        assert kinds == ["fun", "signal", "signal"] * 5


def test_every_game_covers_axes_twice_and_categories_once():
    for game in GAMES:
        counts = Counter(
            target
            for qid in game
            if QUESTIONS[qid]["kind"] == "signal"
            for target in covered_targets(QUESTIONS[qid])
        )
        assert all(counts[axis] >= 2 for axis in AXES)
        assert all(counts[category] >= 1 for category in CATEGORIES)


def test_pool_that_cannot_be_balanced_fails_loudly():
    """재미 문항에는 카테고리 태그가 없어 균형 조건을 채울 수 없다. 조용히 치우친 게임을 만들면 안 된다."""
    fun_only = [question for question in ALL_QUESTIONS if question["kind"] == "fun"]
    with pytest.raises(RuntimeError):
        _pick_balanced(fun_only, 10, random.Random(0))


def test_every_question_can_appear():
    appeared = {qid for game in GAMES for qid in game}
    assert appeared == set(QUESTIONS)


def test_games_are_varied():
    assert len({frozenset(game) for game in GAMES}) > 1900
