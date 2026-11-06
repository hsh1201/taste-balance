import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.config import BASE_DIR, Settings


def make_settings(**overrides) -> Settings:
    values = {"base_url": "https://a.example", "database_url": "sqlite://", "secret_key": "x" * 32}
    return Settings(_env_file=None, **(values | overrides))


def test_relative_sqlite_path_is_anchored_to_the_project_root():
    """cron처럼 작업 폴더가 달라도 앱과 같은 DB 파일을 써야 한다."""
    resolved = make_settings(database_url="sqlite:///./taste_balance.db").resolved_database_url
    assert make_url(resolved).database == str(BASE_DIR / "taste_balance.db")


def test_absolute_sqlite_path_is_kept():
    path = (BASE_DIR / "elsewhere.db").as_posix()
    assert make_url(make_settings(database_url=f"sqlite:///{path}").resolved_database_url).database == path


def test_memory_database_is_kept():
    assert make_settings(database_url="sqlite://").resolved_database_url == "sqlite://"


def test_base_url_loses_its_trailing_slash():
    assert make_settings(base_url="https://a.example/").base_url == "https://a.example"


@pytest.mark.parametrize(("base_url", "secure"), [("https://a.example", True), ("http://127.0.0.1:8000", False)])
def test_cookies_are_secure_only_on_https(base_url, secure):
    assert make_settings(base_url=base_url).cookie_secure is secure


def test_llm_settings_are_optional(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    settings = make_settings()
    assert settings.llm_api_key == ""
    assert settings.llm_model == "claude-haiku-5-5"


def test_placeholder_secret_key_is_rejected():
    with pytest.raises(ValidationError):
        make_settings(secret_key="change-me")
