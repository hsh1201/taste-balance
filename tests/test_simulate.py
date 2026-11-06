import copy
import random
from collections import Counter

import pytest

from app.models import BUDGET_MAX, BUDGET_MIN
from app.services import recommend
from app.services.persona import load_personas
from app.services.recommend import RECOMMENDATION_COUNT, load_gifts, overlaps_budget
from app.services.tags import CATEGORIES
from scripts import simulate_recommend
from scripts.simulate_recommend import Simulation, find_problems, main, random_budget, simulate

GIFT_NAMES = {gift["name"] for gift in load_gifts()}
BUDGET = (30_000, 70_000)


def test_every_game_adds_eight_recommendations():
    result = simulate(30, random.Random(1))
    assert result.games == 30
    assert result.total_picks == 30 * RECOMMENDATION_COUNT
    assert sum(result.categories.values()) == result.total_picks
    assert sum(result.personas.values()) == 30
    assert set(result.personas) <= set(load_personas())
    assert set(result.picked) <= GIFT_NAMES


def test_same_seed_gives_the_same_result():
    assert simulate(20, random.Random(3)) == simulate(20, random.Random(3))


def test_different_seeds_give_different_results():
    assert simulate(20, random.Random(3)).picked != simulate(20, random.Random(4)).picked


def test_gift_is_never_picked_more_often_than_it_was_eligible():
    result = simulate(50, random.Random(2))
    assert all(result.picked[name] <= result.eligible[name] for name in result.picked)


def test_fixed_budget_only_counts_gifts_inside_it():
    gifts_in_budget = {gift["name"] for gift in load_gifts() if overlaps_budget(gift, *BUDGET)}
    result = simulate(20, random.Random(5), BUDGET)
    assert set(result.eligible) == gifts_in_budget
    assert set(result.eligible.values()) == {20}
    assert set(result.picked) <= gifts_in_budget


@pytest.mark.parametrize("seed", range(50))
def test_random_budget_is_a_valid_slider_choice(seed):
    low, high = random_budget(random.Random(seed))
    assert BUDGET_MIN <= low < high <= BUDGET_MAX
    assert low % 10_000 == 0
    assert high % 10_000 == 0


def even_result() -> Simulation:
    names = [f"선물{i}" for i in range(100)]
    return Simulation(
        games=100,
        picked=Counter(dict.fromkeys(names, 8)),
        eligible=Counter(dict.fromkeys(names, 50)),
        even=Counter(dict.fromkeys(names, 8)),
        categories=Counter(dict.fromkeys(CATEGORIES, 100)),
    )


def boosted(gifts: list[dict], names: set[str], **tags: int) -> list[dict]:
    changed = copy.deepcopy(gifts)
    for gift in changed:
        if gift["name"] in names or gift["category"] in names:
            gift["tags"].update(tags)
    return changed


def use_gifts(monkeypatch, gifts: list[dict]) -> None:
    monkeypatch.setattr(recommend, "load_gifts", lambda: gifts)
    monkeypatch.setattr(simulate_recommend, "load_gifts", lambda: gifts)


def test_evenly_spread_result_has_no_problems():
    assert find_problems(even_result()) == []


def test_gift_picked_in_most_eligible_games_is_flagged():
    result = even_result()
    result.picked["선물0"] = 40
    assert any("선물0" in problem for problem in find_problems(result))


def test_high_rate_within_its_fair_share_is_not_flagged():
    """후보가 적은 좁은 예산에서는 공정한 몫 자체가 크므로 과반이어도 쏠림이 아니다."""
    result = even_result()
    result.picked["선물0"] = 30
    result.even["선물0"] = 20
    assert not any("선물0" in problem for problem in find_problems(result))


def test_rarely_eligible_gift_is_not_judged_on_a_small_sample():
    result = even_result()
    result.picked["희귀 선물"] = 3
    result.eligible["희귀 선물"] = 3
    assert not any("희귀 선물" in problem for problem in find_problems(result))


def test_top_ten_taking_over_is_flagged():
    names = [f"선물{i}" for i in range(100)]
    result = Simulation(
        games=100,
        picked=Counter({name: (50 if index < 10 else 1) for index, name in enumerate(names)}),
        eligible=Counter(dict.fromkeys(names, 200)),
        even=Counter(dict.fromkeys(names, 6)),
        categories=Counter(dict.fromkeys(CATEGORIES, 75)),
    )
    assert any("상위 10개" in problem for problem in find_problems(result))


def test_category_taking_every_slot_is_flagged():
    result = even_result()
    result.categories["tech"] = 300
    result.categories["food"] = 0
    assert any("테크/가젯" in problem for problem in find_problems(result))


def test_even_share_adds_up_to_the_number_of_cards():
    result = simulate(20, random.Random(6))
    assert sum(result.even.values()) == pytest.approx(result.total_picks)


def test_real_concentration_is_caught_when_one_gift_always_wins(monkeypatch):
    use_gifts(monkeypatch, boosted(load_gifts(), {"러그"}, minimal=20, object=20))
    assert any("러그" in problem for problem in find_problems(simulate(100, random.Random(0))))


def test_real_concentration_is_caught_when_one_category_always_wins(monkeypatch):
    use_gifts(monkeypatch, boosted(load_gifts(), {"tech"}, minimal=20, object=20, solo=20, home=20))
    assert any("테크/가젯" in problem for problem in find_problems(simulate(100, random.Random(0))))


def test_low_coverage_is_flagged():
    result = even_result()
    result.eligible.update({f"안 뽑힌 선물{i}": 50 for i in range(100)})
    assert any("추천됨" in problem for problem in find_problems(result))


@pytest.mark.parametrize("seed", range(10))
def test_catalog_passes_the_default_check(seed):
    """기본 설정(무작위 응답 100개)으로 실제 카탈로그가 기준을 통과해야 한다."""
    assert find_problems(simulate(100, random.Random(seed))) == []


@pytest.mark.parametrize("budget", [(10_000, 30_000), (30_000, 70_000), (50_000, 100_000), (100_000, 200_000), (200_000, 300_000)], ids=str)
def test_catalog_passes_with_each_typical_budget(budget):
    assert find_problems(simulate(100, random.Random(0), budget)) == []


def test_main_reports_success_and_prints_every_section(capsys):
    assert main(["--games", "100", "--seed", "1"]) == 0
    printed = capsys.readouterr().out
    for section in ("무작위 응답 100개", "시드 1", "[많이 추천된 선물]", "[상위 10개 비중]", "[카테고리별 추천 수]", "[유형 분포]"):
        assert section in printed
    assert "특정 선물에 쏠리지 않음" in printed


def test_main_accepts_a_fixed_budget(capsys):
    assert main(["--games", "100", "--seed", "1", "--budget", "30000", "70000"]) == 0
    assert "30,000~70,000원" in capsys.readouterr().out


def test_main_returns_failure_when_recommendations_are_concentrated(capsys, monkeypatch):
    monkeypatch.setattr(simulate_recommend, "MAX_PICK_RATE", 0.01)
    monkeypatch.setattr(simulate_recommend, "MAX_PICK_RATIO", 0.01)
    assert main(["--games", "100", "--seed", "1"]) == 1
    assert "쏠림 있음" in capsys.readouterr().out
