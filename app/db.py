from collections.abc import Iterator

from sqlalchemy import Engine, event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401  테이블 등록을 위해 불러온다.
from app.config import settings

# Neon은 쉬고 있던 DB를 깨우는 데 시간이 조금 걸리므로 너무 짧게 잡지 않는다.
CONNECT_TIMEOUT_SECONDS = 10


def enable_sqlite_foreign_keys(engine: Engine) -> None:
    """SQLite는 연결마다 켜야 ON DELETE CASCADE가 동작한다."""
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_foreign_keys(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def build_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        built = create_engine(url, connect_args={"check_same_thread": False})
        enable_sqlite_foreign_keys(built)
        return built
    # 서버리스 인스턴스가 연결을 붙잡고 있지 않도록 연결 관리는 Neon 같은 외부 풀러에 맡긴다.
    # 트랜잭션 방식 풀러에서는 psycopg의 prepared statement가 충돌하므로 끈다.
    # DB에 닿지 않을 때 함수가 실행 시간 제한까지 붙잡혀 있지 않게 접속 대기를 줄인다.
    return create_engine(
        url,
        poolclass=NullPool,
        connect_args={"prepare_threshold": None, "connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )


engine = build_engine(settings.resolved_database_url)


def init_db() -> None:
    try:
        SQLModel.metadata.create_all(engine)
    except DBAPIError:
        # 처음 배포할 때 여러 인스턴스가 동시에 테이블을 만들면 충돌할 수 있다.
        # 다시 실행하면 이미 만들어진 테이블은 건너뛴다.
        SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
