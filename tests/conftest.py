import os

# app.config를 불러오기 전에 설정해 로컬 .env의 영향을 받지 않게 한다.
os.environ["BASE_URL"] = "https://test.example"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-settings"
# 로컬 .env에 키가 있어도 테스트가 실제 LLM을 호출해 비용을 쓰는 일이 없게 한다.
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_MODEL"] = "claude-haiku-5-5"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db import enable_sqlite_foreign_keys, get_session
from app.main import app
from app.security import ai_comment_limiter, create_limiter, lookup_limiter, submit_limiter
from app.services import ai_comment


@pytest.fixture
def engine():
    test_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    enable_sqlite_foreign_keys(test_engine)
    SQLModel.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as db_session:
        yield db_session


@pytest.fixture(autouse=True)
def reset_rate_limits():
    create_limiter.reset()
    lookup_limiter.reset()
    submit_limiter.reset()
    ai_comment_limiter.reset()
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
