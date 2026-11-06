import os

# app.config를 불러오기 전에 설정해 로컬 .env의 영향을 받지 않게 한다.
os.environ["BASE_URL"] = "https://test.example"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-settings"
# 로컬 .env에 키가 있어도 테스트가 실제 LLM을 호출해 비용을 쓰는 일이 없게 한다.
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_MODEL"] = "claude-haiku-5-5"
os.environ["CRON_SECRET"] = ""
os.environ.pop("VERCEL", None)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import normalized_database_url
from app.db import build_engine, enable_sqlite_foreign_keys, get_session
from app.main import app
from app.security import lookup_limiter
from app.services import ai_comment

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def engine():
    if TEST_DATABASE_URL:
        # 실제 Postgres로 전체 테스트를 돌릴 때 쓴다. 표를 지웠다 만드니 전용 DB만 지정할 것.
        test_engine = build_engine(normalized_database_url(TEST_DATABASE_URL).render_as_string(hide_password=False))
        SQLModel.metadata.drop_all(test_engine)
    else:
        test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        enable_sqlite_foreign_keys(test_engine)
    SQLModel.metadata.create_all(test_engine)
    yield test_engine
    if TEST_DATABASE_URL:
        SQLModel.metadata.drop_all(test_engine)
    test_engine.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as db_session:
        yield db_session


@pytest.fixture(autouse=True)
def reset_rate_limits():
    lookup_limiter.reset()
    ai_comment.reset_state()


@pytest.fixture
def client(engine):
    def override_get_session():
        with Session(engine) as db_session:
            yield db_session

    app.dependency_overrides[get_session] = override_get_session
    # BASE_URL이 https라 쿠키에 Secure가 붙으므로 같은 주소로 요청한다.
    with TestClient(app, base_url="https://test.example") as test_client:
        yield test_client
    app.dependency_overrides.clear()
