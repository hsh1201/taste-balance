import json
from functools import lru_cache

from app.config import BASE_DIR
from app.services.taste import axis_positions

# 세 갈림길의 조합으로 8유형을 가른다: (축, 왼쪽 이름, 오른쪽 이름, 동점일 때 볼 축)
# 문항이 적은 게임에서는 정확히 반반이 자주 나오므로, 성격이 가까운 축으로 한쪽에 몰리지 않게 가른다.
SPLITS = (
    ("practical_emotional", "practical", "emotional", "minimal_maximal"),
    ("object_experience", "object", "experience", "home_outdoor"),
    ("solo_together", "solo", "together", "classic_trendy"),
)


@lru_cache
def load_personas() -> dict[str, dict]:
    path = BASE_DIR / "data" / "personas.json"
    return json.loads(path.read_text(encoding="utf-8"))


def determine_persona(answers: list[dict]) -> str:
    positions = axis_positions(answers)
    sides = []
    for axis, left, right, tie_axis in SPLITS:
        position = positions[axis] if positions[axis] != 0.5 else positions[tie_axis]
        sides.append(right if position > 0.5 else left)
    return "_".join(sides)


def persona_of(persona_type: str, answers: list[dict]) -> dict:
    """저장된 유형의 내용을 돌려준다. 유형 구성이 바뀌기 전에 저장된 응답은 답변으로 다시 판정한다."""
    personas = load_personas()
    return personas.get(persona_type) or personas[determine_persona(answers)]
