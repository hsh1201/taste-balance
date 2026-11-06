"""무작위 응답을 시뮬레이션해 추천이 특정 선물에 쏠리지 않는지 점검한다.

사용법:
    python scripts/simulate_recommend.py                       # 무작위 응답 100개, 예산도 무작위
    python scripts/simulate_recommend.py --games 1000 --seed 7
    python scripts/simulate_recommend.py --budget 30000 70000  # 예산 고정

쏠림 기준을 넘으면 종료 코드 1을 돌려준다.
"""

import argparse
import random
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# 프로젝트 루트 밖에서 파일을 직접 실행해도 app을 불러올 수 있게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import BUDGET_MAX, BUDGET_MIN  # noqa: E402
from app.services.persona import determine_persona, load_personas  # noqa: E402
from app.services.questions import pick_question_ids  # noqa: E402
from app.services.recommend import RECOMMENDATION_COUNT, build_report, load_gifts, overlaps_budget  # noqa: E402
from app.services.tags import CATEGORIES  # noqa: E402

BUDGET_STEP = 10_000

# "공정한 몫"은 판마다 후보 전원이 같은 확률로 뽑힌다고 할 때의 값이다. 예산이 좁으면 후보가 적어
# 고르게 추천해도 비율이 커지므로, 고정값만으로 판정하면 정상인 결과도 쏠림으로 나온다.

# 추천될 수 있었던 판의 절반을 넘고 공정한 몫의 2배도 넘으면 그 선물에 쏠린 것으로 본다.
MAX_PICK_RATE = 0.5
MAX_PICK_RATIO = 2.0
# 후보가 된 판이 이보다 적으면 우연이 커서 선물별 판정에서 뺀다.
MIN_ELIGIBLE_GAMES = 20
# 상위 10개 비중은 공정한 몫의 2배까지 허용한다.
MAX_TOP10_RATIO = 2.0
# 카테고리당 2개 제한 때문에 한 카테고리의 상한은 25%다. 항상 상한에 가까우면 그 카테고리가 칸을 독차지하는 것이다.
MAX_CATEGORY_SHARE = 0.22
# 후보가 된 적 있는 선물 중 실제로 한 번 이상 추천된 비율.
MIN_COVERAGE = 0.7


@dataclass
class Simulation:
    games: int = 0
    picked: Counter = field(default_factory=Counter)
    eligible: Counter = field(default_factory=Counter)
    # 판마다 후보 전원이 같은 확률로 뽑힌다고 할 때의 기대 추천 수.
    even: Counter = field(default_factory=Counter)
    categories: Counter = field(default_factory=Counter)
    personas: Counter = field(default_factory=Counter)

    def pick_rate(self, name: str) -> float:
        return self.picked[name] / self.eligible[name] if self.eligible[name] else 0.0

    def fair_rate(self, name: str) -> float:
        return self.even[name] / self.eligible[name] if self.eligible[name] else 0.0

    def category_share(self, category: str) -> float:
        return self.categories[category] / self.total_picks

    @property
    def total_picks(self) -> int:
        return sum(self.picked.values())

    @property
    def top10_share(self) -> float:
        return sum(count for _, count in self.picked.most_common(10)) / self.total_picks

    @property
    def even_top10_share(self) -> float:
        return sum(count for _, count in self.even.most_common(10)) / sum(self.even.values())

    @property
    def top10_ratio(self) -> float:
        return self.top10_share / self.even_top10_share

    @property
    def coverage(self) -> float:
        return len(self.picked) / len(self.eligible)


def random_answers(rng: random.Random) -> list[dict]:
    return [{"question_id": question_id, "choice": rng.randint(0, 1)} for question_id in pick_question_ids(rng)]


def random_budget(rng: random.Random) -> tuple[int, int]:
    """슬라이더에서 고를 수 있는 값 중 서로 다른 두 값을 예산으로 쓴다."""
    low, high = sorted(rng.sample(range(BUDGET_MIN, BUDGET_MAX + BUDGET_STEP, BUDGET_STEP), 2))
    return low, high


def simulate(games: int, rng: random.Random, budget: tuple[int, int] | None = None) -> Simulation:
    result = Simulation(games=games)
    for _ in range(games):
        answers = random_answers(rng)
        budget_min, budget_max = budget or random_budget(rng)
        report = build_report(answers, budget_min, budget_max)
        candidates = [gift["name"] for gift in load_gifts() if overlaps_budget(gift, budget_min, budget_max)]
        result.eligible.update(candidates)
        result.even.update(dict.fromkeys(candidates, RECOMMENDATION_COUNT / len(candidates)))
        result.picked.update(card["name"] for card in report["recommendations"])
        result.categories.update(card["category"] for card in report["recommendations"])
        result.personas[determine_persona(answers)] += 1
    return result


def find_problems(result: Simulation) -> list[str]:
    problems = [
        f"{name}: 추천될 수 있었던 {result.eligible[name]}판 중 {result.pick_rate(name):.0%}에 나옴 "
        f"(공정한 몫 {result.fair_rate(name):.0%}, 기준은 {MAX_PICK_RATE:.0%}와 {MAX_PICK_RATIO}배 중 큰 값)"
        for name in result.eligible
        if result.eligible[name] >= MIN_ELIGIBLE_GAMES
        and result.pick_rate(name) > max(MAX_PICK_RATE, MAX_PICK_RATIO * result.fair_rate(name))
    ]
    problems += [
        f"{label}: 전체 추천의 {result.category_share(category):.0%}를 차지함 (기준 {MAX_CATEGORY_SHARE:.0%})"
        for category, label in CATEGORIES.items()
        if result.category_share(category) > MAX_CATEGORY_SHARE
    ]
    if result.top10_ratio > MAX_TOP10_RATIO:
        problems.append(
            f"상위 10개 선물이 전체 추천의 {result.top10_share:.0%}를 차지함 "
            f"(공정한 몫 {result.even_top10_share:.0%}의 {result.top10_ratio:.1f}배, 기준 {MAX_TOP10_RATIO}배)"
        )
    if result.coverage < MIN_COVERAGE:
        problems.append(f"후보였던 선물 중 {result.coverage:.0%}만 추천됨 (기준 {MIN_COVERAGE:.0%})")
    return problems


def print_summary(result: Simulation) -> None:
    print(f"\n[많이 추천된 선물] (전체 추천 {result.total_picks}건)")
    print("  횟수  후보였던 판  비율  공정한 몫  선물")
    for name, count in result.picked.most_common(10):
        print(f"  {count:>4}  {result.eligible[name]:>10}  {result.pick_rate(name):>4.0%}  {result.fair_rate(name):>8.0%}  {name}")

    print(
        f"\n[상위 10개 비중] {result.top10_share:.1%} "
        f"(공정한 몫 {result.even_top10_share:.1%}의 {result.top10_ratio:.1f}배, 기준 {MAX_TOP10_RATIO}배 이하)"
    )
    print(f"[추천된 선물] 후보 {len(result.eligible)}개 중 {len(result.picked)}개 ({result.coverage:.0%})")

    print(f"\n[카테고리별 추천 수] (기준 {MAX_CATEGORY_SHARE:.0%} 이하)")
    for category, label in CATEGORIES.items():
        print(f"  {label:<8} {result.categories[category]:>4}건  {result.category_share(category):>4.0%}")

    print("\n[유형 분포]")
    personas = load_personas()
    for key, persona in personas.items():
        print(f"  {persona['emoji']} {persona['name']:<8} {result.personas[key]:>4}명")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="무작위 응답으로 추천 쏠림을 점검합니다.")
    parser.add_argument("--games", type=int, default=100, help="시뮬레이션할 응답 수 (기본 100)")
    parser.add_argument("--seed", type=int, help="같은 결과를 다시 보려면 지정합니다.")
    parser.add_argument("--budget", type=int, nargs=2, metavar=("MIN", "MAX"), help="예산을 고정합니다. 없으면 판마다 무작위입니다.")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(1_000_000)
    budget_text = f"{args.budget[0]:,}~{args.budget[1]:,}원" if args.budget else "판마다 무작위"
    print(f"무작위 응답 {args.games}개 / 예산 {budget_text} / 시드 {seed}")

    result = simulate(args.games, random.Random(seed), tuple(args.budget) if args.budget else None)
    print_summary(result)

    problems = find_problems(result)
    if problems:
        print("\n[판정] 쏠림 있음")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\n[판정] 특정 선물에 쏠리지 않음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
