from collections.abc import Iterator

from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401  테이블 등록을 위해 불러온다.
from app.config import settings


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
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    built = create_engine(url, connect_args=connect_args)
    enable_sqlite_foreign_keys(built)
    return built


engine = build_engine(settings.resolved_database_url)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
