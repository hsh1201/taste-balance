from collections import Counter

from app.services.questions import load_questions
from app.services.tags import AXES


def answered_choices(answers: list[dict]) -> list[tuple[dict, dict]]:
    """(문항, 고른 선택지) 목록을 돌려준다. 풀에서 사라진 문항은 건너뛴다."""
    questions = load_questions()
    return [
        (questions[answer["question_id"]], questions[answer["question_id"]]["choices"][answer["choice"]])
        for answer in answers
        if answer["question_id"] in questions
    ]


def taste_vector(answers: list[dict]) -> Counter:
    vector = Counter()
    for _question, choice in answered_choices(answers):
        vector.update(choice["tags"])
    return vector


def _axis_value(tags: dict, axis: str) -> int:
    left, right = AXES[axis]
    return tags.get(right, 0) - tags.get(left, 0)


def axis_positions(answers: list[dict]) -> dict[str, float]:
    """축마다 0(왼쪽 극)~1(오른쪽 극) 사이의 위치를 돌려준다.

    게임마다 문항 구성이 달라 점수 합을 그대로 비교할 수 없으므로,
    그 게임에서 낼 수 있었던 최솟값~최댓값 범위로 정규화한다.
    """
    answered = answered_choices(answers)
    positions = {}
    for axis in AXES:
        lowest = highest = actual = 0
        for question, choice in answered:
            values = [_axis_value(option["tags"], axis) for option in question["choices"]]
            lowest += min(values)
            highest += max(values)
            actual += _axis_value(choice["tags"], axis)
        positions[axis] = (actual - lowest) / (highest - lowest) if highest > lowest else 0.5
    return positions
