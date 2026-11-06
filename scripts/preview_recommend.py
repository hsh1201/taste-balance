"""무작위 응답으로 만든 추천 결과를 터미널에서 미리 본다.

사용법 (프로젝트 루트에서):
    python -m scripts.preview_recommend [최소 예산] [최대 예산] [시드]
"""

import random
import sys

from app.services.questions import load_questions, pick_question_ids
from app.services.recommend import build_report


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    budget_min = int(sys.argv[1]) if len(sys.argv) > 1 else 30_000
    budget_max = int(sys.argv[2]) if len(sys.argv) > 2 else 70_000
    rng = random.Random(int(sys.argv[3])) if len(sys.argv) > 3 else random.Random()

    questions = load_questions()
    answers = [{"question_id": qid, "choice": rng.randint(0, 1)} for qid in pick_question_ids(rng)]
    report = build_report(answers, budget_min, budget_max)

    print("[고른 답]")
    for item in answers:
        print(f"  {questions[item['question_id']]['choices'][item['choice']]['label']}")

    print("\n[취향 레이더]")
    for axis in report["radar"]:
        print(f"  {axis['left']} ↔ {axis['right']}: {axis['value']}")

    print(f"\n[키워드] {' '.join('#' + keyword for keyword in report['keywords'])}")

    print(f"\n[추천 {len(report['recommendations'])}개 / 예산 {budget_min:,}~{budget_max:,}원]")
    for card in report["recommendations"]:
        print(f"  {card['emoji']} {card['name']} ({card['category_label']}, {card['price_min']:,}~{card['price_max']:,}원)")
        print(f"     {card['reason']}")
        print(f"     {card['shopping_url']}")

    print("\n[이건 피하세요]")
    for item in report["avoid"]:
        print(f"  {item['label']}: {item['reason']}")


if __name__ == "__main__":
    main()
