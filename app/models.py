from datetime import UTC, datetime, timedelta

from sqlalchemy import JSON, Column, ForeignKey, Integer
from sqlmodel import Field, SQLModel

from app.security import generate_admin_token, generate_slug

SURVEY_TTL_DAYS = 30
RECIPIENT_NAME_MAX_LENGTH = 20
RELATIONS = ("친구", "연인", "가족", "동료")
OCCASIONS = ("생일", "기념일", "감사", "기타")
BUDGET_MIN = 10_000
BUDGET_MAX = 300_000


def utcnow() -> datetime:
    return datetime.now(UTC)


def default_expires_at() -> datetime:
    return utcnow() + timedelta(days=SURVEY_TTL_DAYS)


class Survey(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(default_factory=generate_slug, max_length=8, unique=True, index=True)
    admin_token: str = Field(default_factory=generate_admin_token, unique=True, index=True)
    recipient_name: str = Field(max_length=RECIPIENT_NAME_MAX_LENGTH)
    relation: str
    occasion: str
    budget_min: int
    budget_max: int
    question_ids: list[str] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field(default_factory=default_expires_at)

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= utcnow()


class Response(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    # unique 제약으로 동시에 제출해도 설문당 응답은 하나만 저장된다.
    survey_id: int = Field(
        sa_column=Column(Integer, ForeignKey("survey.id", ondelete="CASCADE"), nullable=False, unique=True)
    )
    answers: list[dict] = Field(sa_column=Column(JSON, nullable=False))
    persona_type: str
    completed_at: datetime = Field(default_factory=utcnow)


class AiComment(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    # 설문당 하나만 저장해 새로고침할 때마다 LLM을 다시 호출하지 않는다.
    survey_id: int = Field(
        sa_column=Column(Integer, ForeignKey("survey.id", ondelete="CASCADE"), nullable=False, unique=True)
    )
    picks: list[dict] = Field(sa_column=Column(JSON, nullable=False))
    model: str
    created_at: datetime = Field(default_factory=utcnow)
