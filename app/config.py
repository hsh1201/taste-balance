import os
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

BASE_DIR = Path(__file__).resolve().parent.parent


def normalized_database_url(database_url: str) -> URL:
    """Postgres 주소를 설치된 드라이버로 연결하도록 맞추고, SQLite 상대 경로를 고정한다."""
    url = make_url(database_url)
    # 호스팅 업체가 주는 postgres:// 주소는 SQLAlchemy가 드라이버를 알 수 없다.
    if url.drivername in {"postgres", "postgresql"}:
        return url.set(drivername="postgresql+psycopg")
    # cron처럼 작업 폴더가 달라도 같은 SQLite 파일을 쓰도록 상대 경로를 고정한다.
    is_file_sqlite = url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:")
    if is_file_sqlite and not Path(url.database).is_absolute():
        return url.set(database=str(BASE_DIR / url.database))
    return url


class Settings(BaseSettings):
    # Windows 편집기가 붙이는 BOM도 읽을 수 있도록 utf-8-sig를 쓴다.
    # 설정 오류가 로그에 찍힐 때 비밀 키와 DB 비밀번호가 함께 나오지 않게 한다.
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8-sig", extra="ignore", hide_input_in_errors=True
    )

    base_url: str = ""
    database_url: str
    secret_key: str = Field(min_length=32)
    # 비어 있으면 AI 선물 코멘트 섹션을 보여주지 않는다.
    llm_api_key: str = ""
    llm_model: str = "claude-haiku-5-5"
    # 비어 있으면 만료 데이터 정리 경로를 열지 않는다. Vercel Cron이 이 값으로 인증한다.
    cron_secret: str = ""

    @field_validator("base_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def check_deployment(self) -> "Settings":
        on_vercel = bool(os.environ.get("VERCEL"))
        if on_vercel and make_url(self.database_url).get_backend_name() == "sqlite":
            # /tmp로 옮겨도 인스턴스마다 파일이 따로라 만들어 둔 링크가 사라진다.
            raise ValueError("Vercel에서는 SQLite를 쓸 수 없습니다. DATABASE_URL에 Postgres 주소를 넣어 주세요.")
        if not self.base_url:
            production_host = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
            if not production_host:
                raise ValueError("BASE_URL이 필요합니다.")
            self.base_url = f"https://{production_host}"
        return self

    @property
    def cookie_secure(self) -> bool:
        return self.base_url.startswith("https://")

    @property
    def resolved_database_url(self) -> str:
        return normalized_database_url(self.database_url).render_as_string(hide_password=False)


settings = Settings()
