"""만료된 설문과 응답을 삭제한다.

서버의 cron은 작업 폴더가 프로젝트 루트가 아닐 수 있으므로 절대 경로로 실행한다.
    /path/to/venv/bin/python /path/to/scripts/cleanup.py

지우지 않고 개수만 보려면 --dry-run을 붙인다. Vercel에서는 Vercel Cron이 /api/cron/cleanup을 호출한다.
"""

import argparse
import sys
from pathlib import Path

# 프로젝트 루트 밖에서 파일을 직접 실행해도 app을 불러올 수 있게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.models import utcnow  # noqa: E402
from app.services.cleanup import count_expired, delete_expired  # noqa: E402


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="만료된 설문과 응답을 삭제합니다.")
    parser.add_argument("--dry-run", action="store_true", help="지우지 않고 삭제 대상 개수만 보여줍니다.")
    args = parser.parse_args(argv)

    # 앱을 한 번도 띄우지 않은 서버에서 cron이 먼저 돌아도 실패하지 않게 한다.
    init_db()
    now = utcnow()
    with Session(engine) as session:
        if args.dry_run:
            surveys, responses = count_expired(session, now)
            print(f"삭제 대상: 설문 {surveys}개, 응답 {responses}개 (--dry-run이라 지우지 않았습니다)")
        else:
            surveys, responses = delete_expired(session, now)
            print(f"삭제 완료: 설문 {surveys}개, 응답 {responses}개")


if __name__ == "__main__":
    main()
