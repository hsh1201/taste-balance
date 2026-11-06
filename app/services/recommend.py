import json
from collections import Counter, defaultdict
from functools import lru_cache
from statistics import mean
from urllib.parse import quote

from app.config import BASE_DIR
from app.services.tags import AXES, CATEGORIES, POLE_LABELS, SUBTAG_LABELS, SUBTAGS, category_of
from app.services.taste import answered_choices, axis_positions, taste_vector

RECOMMENDATION_COUNT = 8
MAX_PER_CATEGORY = 2

MAX_EVIDENCE_CLAUSES = 2
# 두 번째 인용은 이보다 약하면 뺀다. 억지로 붙인 근거는 오히려 신뢰를 깎는다.
MIN_SECOND_EVIDENCE_SUPPORT = 5
FALLBACK_EVIDENCE = "이번 게임에서 고른 답들과 잘 맞아요."

MAX_AVOID_CATEGORIES = 2
# 두 번째 카테고리는 최하위와 점수가 이만큼 가까울 때만 함께 알려준다.
SECOND_AVOID_MAX_GAP = 2.0
FALLBACK_AVOID_REASON = "다른 분야에 비해 끌리는 선택이 적었어요."

MAX_POLE_KEYWORDS = 3
MAX_INTEREST_KEYWORDS = 3
MIN_POLE_LEAN = 0.15
MIN_INTEREST_WEIGHT = 2

SHOPPING_SEARCH_URL = "https://search.shopping.naver.com/search/all?query="


@lru_cache
def load_gifts() -> list[dict]:
    path = BASE_DIR / "data" / "gifts.json"
    return json.loads(path.read_text(encoding="utf-8"))


def dot_product(weights: dict, tags: dict) -> int:
    return sum(weight * tags.get(tag, 0) for tag, weight in weights.items())


def score_gift(gift: dict, vector: dict) -> int:
    return dot_product(gift["tags"], vector)


def overlaps_budget(gift: dict, budget_min: int, budget_max: int) -> bool:
    return gift["price_min"] <= budget_max and gift["price_max"] >= budget_min


def rank_gifts(gifts: list[dict], vector: dict) -> list[dict]:
    """점수 내림차순으로 정렬한다. 결과 페이지를 다시 열어도 같게 보이도록 동점은 이름순으로 고정한다."""
    scored = [{**gift, "score": score_gift(gift, vector)} for gift in gifts]
    return sorted(scored, key=lambda gift: (-gift["score"], gift["name"]))


def pick_diverse(ranked: list[dict]) -> list[dict]:
    """점수 순으로 담되 같은 카테고리는 최대 MAX_PER_CATEGORY개까지만 담는다."""
    picked = []
    per_category = Counter()
    for gift in ranked:
        if per_category[gift["category"]] < MAX_PER_CATEGORY:
            picked.append(gift)
            per_category[gift["category"]] += 1
            if len(picked) == RECOMMENDATION_COUNT:
                break
    return picked


def signal_contrasts(answers: list[dict]) -> list[tuple[str, dict, dict]]:
    """(문항 id, 고른 선택지, 고르지 않은 선택지) 목록. 재미 문항은 근거로 내세우기엔 가벼워 뺀다."""
    return [
        (question["id"], chosen, next(option for option in question["choices"] if option is not chosen))
        for question, chosen in answered_choices(answers)
        if question["kind"] == "signal"
    ]


def _object_particle(word: str) -> str:
    syllable = ord(word[-1]) - ord("가")
    has_final_consonant = 0 <= syllable <= ord("힣") - ord("가") and syllable % 28 != 0
    return "을" if has_final_consonant else "를"


def contrast_sentence(pairs: list[tuple[dict, dict]]) -> str:
    """(고른 선택지, 고르지 않은 선택지) 쌍으로 "'A'보다 'B'를, 'C'보다 'D'를 골랐어요." 문장을 만든다."""
    clauses = [
        f"'{rejected['label']}'보다 '{chosen['label']}'{_object_particle(chosen['label'])}"
        for chosen, rejected in pairs
    ]
    return f"{', '.join(clauses)} 골랐어요."


def evidence_sentence(gift: dict, contrasts: list[tuple[str, dict, dict]], quoted: Counter) -> str:
    """이 선물 쪽으로 점수를 실제로 끌어올린 선택을 인용한다.

    반대쪽 선택지가 더 잘 맞았던 문항은 근거가 되지 못한다. 카드마다 같은 문장이 반복되지 않도록
    이미 인용한 문항은 설득력을 깎아 다른 근거에 기회를 준다.
    """
    supports = {}
    for question_id, chosen, rejected in contrasts:
        support = dot_product(gift["tags"], chosen["tags"]) - dot_product(gift["tags"], rejected["tags"])
        if support > 0:
            supports[question_id] = (support, chosen, rejected)
    if not supports:
        return FALLBACK_EVIDENCE

    def appeal(question_id: str) -> tuple[float, str]:
        return -supports[question_id][0] / (1 + quoted[question_id]), question_id

    order = sorted(supports, key=appeal)
    picked = order[:1] + [
        question_id
        for question_id in order[1:MAX_EVIDENCE_CLAUSES]
        if supports[question_id][0] >= MIN_SECOND_EVIDENCE_SUPPORT
    ]
    quoted.update(picked)
    # 문장에서는 설득력이 큰 근거를 앞에 둔다.
    picked.sort(key=lambda question_id: -supports[question_id][0])
    return contrast_sentence([supports[question_id][1:] for question_id in picked])


def _recommendation_card(gift: dict, evidence: str) -> dict:
    return {
        "name": gift["name"],
        "emoji": gift["emoji"],
        "category": gift["category"],
        "category_label": CATEGORIES[gift["category"]],
        "price_min": gift["price_min"],
        "price_max": gift["price_max"],
        "score": gift["score"],
        "reason": gift["reason_template"].format(evidence=evidence),
        "shopping_url": SHOPPING_SEARCH_URL + quote(gift["name"]),
    }


def category_scores(ranked: list[dict]) -> dict[str, float]:
    """카테고리별 상위 MAX_PER_CATEGORY개의 평균 점수.

    전체 평균을 쓰면 세부 분야가 많은 취미처럼 캠핑만 좋아해도 점수가 희석되어,
    추천해야 할 카테고리를 피하라고 말하게 된다.
    """
    top_scores = defaultdict(list)
    for gift in ranked:
        if len(top_scores[gift["category"]]) < MAX_PER_CATEGORY:
            top_scores[gift["category"]].append(gift["score"])
    return {category: mean(scores) for category, scores in top_scores.items()}


def _category_weight(tags: dict, category: str) -> int:
    return sum(weight for tag, weight in tags.items() if category_of(tag) == category)


def lowest_categories(scores: dict[str, float], vector: dict) -> list[str]:
    """최하위 카테고리와, 그만큼 낮을 때에 한해 다음 카테고리를 돌려준다.

    직접 골랐던 분야를 피하라고 하면 틀린 조언이 되므로, 그런 분야는 점수가 낮아도 제외한다.
    """
    unchosen = [category for category in scores if _category_weight(vector, category) < MIN_INTEREST_WEIGHT]
    order = sorted(unchosen or scores, key=lambda category: (scores[category], category))
    lowest = order[:1]
    for category in order[1:MAX_AVOID_CATEGORIES]:
        if scores[category] - scores[order[0]] <= SECOND_AVOID_MAX_GAP:
            lowest.append(category)
    return lowest


def _avoid_reason(category: str, contrasts: list[tuple[str, dict, dict]]) -> str:
    """그 카테고리 쪽 선택지를 실제로 밀어낸 문항을 인용한다."""
    gaps = {}
    for question_id, chosen, rejected in contrasts:
        gap = _category_weight(rejected["tags"], category) - _category_weight(chosen["tags"], category)
        if gap > 0:
            gaps[question_id] = (gap, chosen, rejected)
    if not gaps:
        return FALLBACK_AVOID_REASON
    order = sorted(gaps, key=lambda question_id: (-gaps[question_id][0], question_id))
    return contrast_sentence([gaps[question_id][1:] for question_id in order[:MAX_EVIDENCE_CLAUSES]])


def _lean(left: str, right: str, value: int) -> tuple[str, int]:
    """기운 쪽 극의 이름과 정도(50~100)를 돌려준다. 딱 중간이면 두 극을 함께 보여준다."""
    if value > 50:
        return right, value
    if value < 50:
        return left, 100 - value
    return f"{left}·{right}", value


def radar_axes(answers: list[dict]) -> list[dict]:
    """value는 왼쪽 극(0)에서 오른쪽 극(100)까지의 위치다.

    차트에는 기운 쪽을 기준으로 그린다. 한쪽 극을 고정하면 왼쪽으로 기운 사람은 값이 작아져,
    취향이 뚜렷한데도 도형이 쪼그라든 것처럼 보인다.
    """
    positions = axis_positions(answers)
    axes = []
    for axis, (left, right) in AXES.items():
        value = round(positions[axis] * 100)
        lean_label, lean_value = _lean(POLE_LABELS[left], POLE_LABELS[right], value)
        axes.append(
            {
                "axis": axis,
                "left": POLE_LABELS[left],
                "right": POLE_LABELS[right],
                "value": value,
                "lean_label": lean_label,
                "lean_value": lean_value,
            }
        )
    return axes


def taste_keywords(answers: list[dict]) -> list[str]:
    """뚜렷하게 기운 축의 극과, 많이 고른 관심 분야를 키워드로 뽑는다."""
    positions = axis_positions(answers)
    leaning = sorted(
        (axis for axis in AXES if abs(positions[axis] - 0.5) >= MIN_POLE_LEAN),
        key=lambda axis: -abs(positions[axis] - 0.5),
    )
    poles = []
    for axis in leaning[:MAX_POLE_KEYWORDS]:
        left, right = AXES[axis]
        poles.append(POLE_LABELS[right if positions[axis] > 0.5 else left])

    vector = taste_vector(answers)
    interests = [tag for tag in (*SUBTAGS, *CATEGORIES) if vector[tag] >= MIN_INTEREST_WEIGHT]
    # '캠핑'이 잡혔다면 상위 분류인 '취미'까지 보여줄 필요는 없다.
    specified = {SUBTAGS[tag] for tag in interests if tag in SUBTAGS}
    interests = [tag for tag in interests if tag not in specified]
    interests.sort(key=lambda tag: -vector[tag])
    labels = {**CATEGORIES, **SUBTAG_LABELS}
    return poles + [labels[tag] for tag in interests[:MAX_INTEREST_KEYWORDS]]


def build_report(answers: list[dict], budget_min: int, budget_max: int) -> dict:
    """결과 페이지에 필요한 추천 데이터를 만든다. 같은 입력이면 언제나 같은 결과를 돌려준다."""
    vector = taste_vector(answers)
    ranked = rank_gifts(load_gifts(), vector)
    contrasts = signal_contrasts(answers)

    # 취향에 대한 판단이므로 예산과 무관하게 전체 카탈로그로 계산하고,
    # 피하라고 한 카테고리를 추천하는 모순이 없도록 후보에서 먼저 뺀다.
    avoided = lowest_categories(category_scores(ranked), vector)
    candidates = [
        gift
        for gift in ranked
        if gift["category"] not in avoided and overlaps_budget(gift, budget_min, budget_max)
    ]

    quoted = Counter()
    return {
        "radar": radar_axes(answers),
        "keywords": taste_keywords(answers),
        "recommendations": [
            _recommendation_card(gift, evidence_sentence(gift, contrasts, quoted))
            for gift in pick_diverse(candidates)
        ],
        "avoid": [
            {"category": category, "label": CATEGORIES[category], "reason": _avoid_reason(category, contrasts)}
            for category in avoided
        ],
    }
