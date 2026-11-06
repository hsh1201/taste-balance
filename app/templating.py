from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.templating import Jinja2Templates

KST = timezone(timedelta(hours=9))

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def format_won(amount: int) -> str:
    """30000 -> '3만 원'"""
    return f"{amount // 10_000}만 원" if amount % 10_000 == 0 else f"{amount:,}원"


def format_won_range(low: int, high: int) -> str:
    """25000, 60000 -> '2.5만~6만 원'"""
    return f"{low / 10_000:g}만~{high / 10_000:g}만 원"


def format_kst_date(value: datetime) -> str:
    local = value.astimezone(KST)
    return f"{local.year}년 {local.month}월 {local.day}일"


templates.env.filters["won"] = format_won
templates.env.filters["won_range"] = format_won_range
templates.env.filters["kst_date"] = format_kst_date
