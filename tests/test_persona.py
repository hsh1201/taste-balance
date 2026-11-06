import random
from collections import Counter
from itertools import product
from pathlib import Path

import pytest

from app.services.persona import SPLITS, determine_persona, load_personas, persona_of
from app.services.questions import load_questions, pick_question_ids
from app.services.tags import AXES
from app.services.taste import answered_choices, axis_positions, taste_vector

QUESTIONS = load_questions()
PERSONAS = load_personas()
STYLE_CSS = (Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
PERSONA_KEYS = ["_".join(sides) for sides in product(*((left, right) for _axis, left, right, _tie in SPLITS))]


def answers_favoring(tags: tuple[str, ...], seed: int = 0) -> list[dict]:
    """고른 태그의 합이 큰 쪽을 고르고, 동점이면 무작위로 고르는 응답을 만든다."""
    rng = random.Random(seed)
    answers = []
    for question_id in pick_question_ids(rng):
        scores = [sum(choice["tags"].get(tag, 0) for tag in tags) for choice in QUESTIONS[question_id]["choices"]]
        pick = rng.randint(0, 1) if scores[0] == scores[1] else scores.index(max(scores))
        answers.append({"question_id": question_id, "choice": pick})
    return answers


def answer(question_id: str, choice: int) -> dict:
    return {"question_id": question_id, "choice": choice}


def test_taste_vector_sums_chosen_tags_only():
    vector = taste_vector([answer("q10", 0), answer("q19", 1)])
    assert vector == {"solo": 2, "music": 2, "hobby": 1}


def test_taste_vector_adds_up_repeated_tags():
    vector = taste_vector([answer("q10", 0), answer("q22", 0), answer("q19", 0)])
    assert vector == {"solo": 4, "scent": 2}


def test_unknown_question_is_ignored():
    assert answered_choices([answer("removed", 0)]) == []
    assert taste_vector([answer("removed", 1), answer("q10", 0)]) == {"solo": 2}


def test_axis_position_is_one_when_every_choice_leans_right():
    positions = axis_positions([answer("q10", 1), answer("q22", 1)])
    assert positions["solo_together"] == 1.0


def test_axis_position_is_zero_when_every_choice_leans_left():
    positions = axis_positions([answer("q10", 0), answer("q22", 0)])
    assert positions["solo_together"] == 0.0


def test_axis_position_is_half_when_choices_split():
    positions = axis_positions([answer("q10", 0), answer("q22", 1)])
    assert positions["solo_together"] == 0.5


def test_axis_without_questions_stays_in_the_middle():
    positions = axis_positions([answer("q10", 0)])
    assert positions["minimal_maximal"] == 0.5


@pytest.mark.parametrize("seed", range(50))
def test_axis_positions_stay_in_range(seed):
    rng = random.Random(seed)
    answers = [answer(qid, rng.randint(0, 1)) for qid in pick_question_ids(rng)]
    positions = axis_positions(answers)
    assert set(positions) == set(AXES)
    assert all(0 <= value <= 1 for value in positions.values())


def test_there_are_eight_personas_one_for_each_combination():
    assert len(PERSONAS) == 8
    assert set(PERSONAS) == set(PERSONA_KEYS)


@pytest.mark.parametrize("persona", PERSONAS.values(), ids=lambda p: p["name"])
def test_persona_has_name_description_and_emoji_without_revealing_the_purpose(persona):
    assert persona["name"] and persona["emoji"] and persona["tagline"] and persona["description"]
    assert len(persona["traits"]) == 3
    for text in (persona["name"], persona["tagline"], persona["description"], *persona["traits"]):
        assert "선물" not in text


@pytest.mark.parametrize("persona", PERSONAS.values(), ids=lambda p: p["name"])
def test_persona_theme_has_a_color_in_the_stylesheet(persona):
    assert f".theme-{persona['theme']}" in STYLE_CSS


def test_personas_have_distinct_names_taglines_and_emoji():
    for field in ("name", "tagline", "emoji"):
        assert len({persona[field] for persona in PERSONAS.values()}) == 8


def test_spec_examples_are_among_the_personas():
    names = {persona["name"] for persona in PERSONAS.values()}
    assert {"감성 수집가", "실용 탐험가"} <= names


def test_tie_on_a_split_is_decided_by_the_related_axis():
    split = [answer("q10", 0), answer("q22", 1)]
    assert determine_persona(split).endswith("_solo")
    # 사람과 트렌드는 가까운 축이라, 혼자/함께가 반반이면 트렌디 쪽 응답을 함께로 본다.
    assert determine_persona([*split, answer("f07", 1)]).endswith("_together")
    assert determine_persona([*split, answer("f07", 0)]).endswith("_solo")


def test_stored_persona_is_used_as_it_is():
    answers = [answer("q10", 1)]
    assert persona_of("emotional_object_solo", answers) == PERSONAS["emotional_object_solo"]


@pytest.mark.parametrize("old_type", ["emotional_homebody", "practical_social", "emotional_curator"])
def test_response_saved_before_the_personas_changed_is_judged_again(old_type):
    answers = [answer("q10", 1), answer("q22", 1)]
    assert persona_of(old_type, answers) == PERSONAS[determine_persona(answers)]


@pytest.mark.parametrize("key", PERSONA_KEYS)
def test_clear_tendencies_map_to_the_expected_persona(key):
    tags = tuple(key.split("_"))
    results = Counter(determine_persona(answers_favoring(tags, seed=seed)) for seed in range(60))
    assert results.most_common(1)[0][0] == key


def test_random_players_reach_every_persona_without_heavy_skew():
    rng = random.Random(7)
    games = 4000
    results = Counter(
        determine_persona([answer(qid, rng.randint(0, 1)) for qid in pick_question_ids(rng)])
        for _ in range(games)
    )
    assert set(results) == set(PERSONAS)
    assert max(results.values()) / games < 0.2
    assert min(results.values()) / games > 0.07
