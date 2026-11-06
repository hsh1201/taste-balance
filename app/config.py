from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Windows 편집기가 붙이는 BOM도 읽을 수 있도록 utf-8-sig를 쓴다.
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8-sig", extra="ignore")

    base_url: str
    database_url: str
    secret_key: str = Field(min_length=32)
    # 비어 있으면 AI 선물 코멘트 섹션을 보여주지 않는다.
    llm_api_key: str = ""
    llm_model: str = "claude-haiku-5-5"

    @field_validator("base_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def cookie_secure(self) -> bool:
        return self.base_url.startswith("https://")

    @property
    def resolved_database_url(self) -> str:
        """cron처럼 작업 폴더가 달라도 같은 SQLite 파일을 쓰도록 상대 경로를 고정한다."""
        url = make_url(self.database_url)
        is_file_sqlite = url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:")
        if is_file_sqlite and not Path(url.database).is_absolute():
            url = url.set(database=str(BASE_DIR / url.database))
        return url.render_as_string(hide_password=False)


settings = Settings()
