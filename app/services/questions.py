import json
import random
from collections import Counter
from functools import lru_cache

from app.config import BASE_DIR
from app.services.tags import AXES, CATEGORIES, category_of

SIGNAL_COUNT = 10
FUN_COUNT = 5
SIGNALS_PER_FUN = SIGNAL_COUNT // FUN_COUNT

MIN_QUESTIONS_PER_AXIS = 2
MIN_QUESTIONS_PER_CATEGORY = 1
MAX_PICK_ATTEMPTS = 1000


@lru_cache
def load_questions() -> dict[str, dict]:
    path = BASE_DIR / "data" / "questions.json"
    return {question["id"]: question for question in json.loads(path.read_text(encoding="utf-8"))}


def covered_targets(question: dict) -> set[str]:
    """선택에 따라 값이 달라지는 축과 카테고리만 그 문항이 다루는 대상으로 친다."""
    first, second = (choice["tags"] for choice in question["choices"])
    targets = set()
    for axis, (left, right) in AXES.items():
        if first.get(right, 0) - first.get(left, 0) != second.get(right, 0) - second.get(left, 0):
            targets.add(axis)
    for tag in first.keys() | second.keys():
        category = category_of(tag)
        if category and first.get(tag, 0) != second.get(tag, 0):
            targets.add(category)
    return targets


def is_balanced(questions: list[dict]) -> bool:
    counts = Counter(target for question in questions for target in covered_targets(question))
    return all(counts[axis] >= MIN_QUESTIONS_PER_AXIS for axis in AXES) and all(
        counts[category] >= MIN_QUESTIONS_PER_CATEGORY for category in CATEGORIES
    )


def _pick_balanced(pool: list[dict], count: int, rng: random.Random) -> list[dict]:
    """모든 문항에 고른 기회를 주려고, 무작위로 뽑아 균형 조건을 통과할 때까지 반복한다."""
    for _ in range(MAX_PICK_ATTEMPTS):
        picked = rng.sample(pool, count)
        if is_balanced(picked):
            return picked
    raise RuntimeError("균형 조건을 만족하는 문항 조합을 찾지 못했습니다. questions.json을 확인하세요.")


def pick_question_ids(rng: random.Random | None = None) -> list[str]:
    """재미 문항으로 시작해 일반 문항 2개마다 재미 문항 1개가 끼도록 15문항을 뽑는다."""
    rng = rng or random.SystemRandom()
    questions = load_questions().values()
    signal = _pick_balanced([q for q in questions if q["kind"] == "signal"], SIGNAL_COUNT, rng)
    fun = rng.sample([q for q in questions if q["kind"] == "fun"], FUN_COUNT)

    ordered = []
    for index, fun_question in enumerate(fun):
        start = index * SIGNALS_PER_FUN
        ordered += [fun_question, *signal[start : start + SIGNALS_PER_FUN]]
    return [question["id"] for question in ordered]
