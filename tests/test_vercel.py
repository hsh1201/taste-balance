import json
import re
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import Request
from pydantic import ValidationError
from sqlalchemy.pool import NullPool
from sqlmodel import select

from app import db
from app.config import Settings, normalized_database_url, settings
from app.db import build_engine
from app.models import RateLimitHit, Response, Survey, utcnow
from app.rate_limit import SharedRateLimiter, create_limiter
from app.security import client_ip
from app.services.cleanup import RATE_LIMIT_RETENTION_SECONDS

ROOT = Path(__file__).resolve().parent.parent
CRON_PATH = "/api/cron/cleanup"
POSTGRES = "postgres://user:test-password@db-pooler.example.test/neondb?sslmode=require"


def make_settings(**overrides) -> Settings:
    values = {"base_url": "https://a.example", "database_url": "sqlite://", "secret_key": "x" * 32}
    return Settings(_env_file=None, **(values | overrides))


def request_with(headers: dict[str, str], host: str = "10.0.0.1") -> Request:
    scope = {
        "type": "http",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        "client": (host, 1234),
    }
    return Request(scope)


# ---------- 설정 ----------


@pytest.mark.parametrize("scheme", ["postgres", "postgresql"])
def test_hosted_postgres_url_uses_the_installed_driver(scheme):
    url = normalized_database_url(f"{scheme}://u:p@host/db?sslmode=require")
    assert url.drivername == "postgresql+psycopg"
    assert url.query["sslmode"] == "require"
    assert url.password == "p"


def test_postgres_url_with_explicit_driver_is_kept():
    assert normalized_database_url("postgresql+psycopg://u:p@host/db").drivername == "postgresql+psycopg"


def test_resolved_url_keeps_the_password_for_connecting():
    assert "test-password" in make_settings(database_url=POSTGRES).resolved_database_url


def test_sqlite_is_refused_on_vercel_with_a_clear_message(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    with pytest.raises(ValidationError, match="Postgres"):
        make_settings(database_url="sqlite:///./taste_balance.db")


def test_sqlite_is_fine_elsewhere(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    assert make_settings(database_url="sqlite:///./taste_balance.db")


def test_postgres_is_accepted_on_vercel(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    assert make_settings(database_url=POSTGRES)


def test_base_url_defaults_to_the_vercel_production_address(monkeypatch):
    monkeypatch.delenv("BASE_URL", raising=False)
    monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "taste-balance-example.vercel.app")
    built = make_settings(base_url="", database_url=POSTGRES)
    assert built.base_url == "https://taste-balance-example.vercel.app"
    assert built.cookie_secure is True


def test_explicit_base_url_wins_over_the_vercel_address(monkeypatch):
    monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "taste-balance-example.vercel.app")
    assert make_settings(base_url="https://my.example").base_url == "https://my.example"


def test_base_url_is_required_outside_vercel(monkeypatch):
    monkeypatch.delenv("VERCEL_PROJECT_PRODUCTION_URL", raising=False)
    monkeypatch.delenv("BASE_URL", raising=False)
    with pytest.raises(ValidationError, match="BASE_URL"):
        make_settings(base_url="")


def test_settings_errors_never_print_secrets(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    with pytest.raises(ValidationError) as caught:
        make_settings(database_url="sqlite:///x.db", secret_key="my-very-secret-signing-key-0123456789", llm_api_key="sk-secret")
    message = str(caught.value)
    assert "my-very-secret-signing-key" not in message
    assert "sk-secret" not in message


def test_short_secret_error_does_not_print_the_value():
    with pytest.raises(ValidationError) as caught:
        make_settings(secret_key="short-secret-value")
    assert "short-secret-value" not in str(caught.value)


# ---------- DB 연결 ----------


def test_postgres_engine_leaves_pooling_to_the_external_pooler():
    engine = build_engine(normalized_database_url(POSTGRES).render_as_string(hide_password=False))
    assert isinstance(engine.pool, NullPool)
    assert engine.dialect.driver == "psycopg"
    engine.dispose()


def test_postgres_engine_is_safe_behind_a_transaction_pooler(monkeypatch):
    captured = {}
    real_create_engine = db.create_engine

    def spy(url, **kwargs):
        captured.update(kwargs)
        return real_create_engine(url, **kwargs)

    monkeypatch.setattr(db, "create_engine", spy)
    build_engine("postgresql+psycopg://u:p@localhost/db").dispose()

    assert captured["connect_args"]["prepare_threshold"] is None
    assert captured["connect_args"]["connect_timeout"] > 0


def test_sqlite_engine_keeps_working():
    engine = build_engine("sqlite://")
    assert engine.dialect.name == "sqlite"
    engine.dispose()


# ---------- 접속자 IP ----------


def test_vercel_uses_the_header_it_overwrites(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    assert client_ip(request_with({"x-real-ip": "203.0.113.7"})) == "203.0.113.7"


def test_vercel_falls_back_to_forwarded_for(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    assert client_ip(request_with({"x-forwarded-for": "203.0.113.8, 10.0.0.1"})) == "203.0.113.8"


def test_vercel_without_headers_uses_the_connection(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    assert client_ip(request_with({})) == "10.0.0.1"


def test_headers_are_not_trusted_outside_vercel(monkeypatch):
    """다른 환경에서는 누구나 이 헤더를 지어낼 수 있다."""
    monkeypatch.delenv("VERCEL", raising=False)
    assert client_ip(request_with({"x-real-ip": "203.0.113.7", "x-forwarded-for": "203.0.113.8"})) == "10.0.0.1"


# ---------- 인스턴스끼리 공유되는 rate limit ----------


def test_limit_is_shared_between_instances(session):
    """서버리스에서 요청이 다른 인스턴스로 가도 횟수가 이어져야 한다."""
    now = [1_000_000.0]
    instance_a = SharedRateLimiter("x", limit=2, window_seconds=600, clock=lambda: now[0])
    instance_b = SharedRateLimiter("x", limit=2, window_seconds=600, clock=lambda: now[0])

    assert instance_a.allow(session, "1.1.1.1") is True
    assert instance_b.allow(session, "1.1.1.1") is True
    assert instance_a.allow(session, "1.1.1.1") is False
    assert instance_b.allow(session, "1.1.1.1") is False


def test_limit_is_per_visitor_and_per_limiter(session):
    now = [1_000_000.0]
    first = SharedRateLimiter("first", limit=1, window_seconds=600, clock=lambda: now[0])
    second = SharedRateLimiter("second", limit=1, window_seconds=600, clock=lambda: now[0])

    assert first.allow(session, "1.1.1.1") is True
    assert first.allow(session, "2.2.2.2") is True
    assert second.allow(session, "1.1.1.1") is True
    assert first.allow(session, "1.1.1.1") is False


def test_limit_recovers_in_the_next_window(session):
    now = [1_000_000.0]
    limiter = SharedRateLimiter("x", limit=1, window_seconds=600, clock=lambda: now[0])
    assert limiter.allow(session, "1.1.1.1") is True
    assert limiter.allow(session, "1.1.1.1") is False
    now[0] += 600
    assert limiter.allow(session, "1.1.1.1") is True


def test_visitor_ip_is_not_stored_as_it_is(session):
    SharedRateLimiter("x", limit=1, window_seconds=600).allow(session, "203.0.113.99")
    stored = session.exec(select(RateLimitHit)).one()
    assert "203.0.113.99" not in stored.bucket
    assert stored.bucket.startswith("x:")


def test_limit_survives_across_requests(client, session, monkeypatch):
    monkeypatch.setattr(create_limiter, "limit", 1)
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/new").text).group(1)
    form = {
        "csrf_token": token,
        "recipient_name": "민지",
        "relation": "친구",
        "occasion": "생일",
        "budget_min": "30000",
        "budget_max": "70000",
    }
    assert client.post("/new", data=form, follow_redirects=False).status_code == 303
    response = client.post("/new", data=form, follow_redirects=False)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "600"


# ---------- Vercel Cron ----------


def add_expired_survey(session) -> Survey:
    survey = Survey(
        recipient_name="민지",
        relation="친구",
        occasion="생일",
        budget_min=30_000,
        budget_max=70_000,
        question_ids=["q01"],
        expires_at=utcnow() - timedelta(days=1),
    )
    session.add(survey)
    session.commit()
    session.refresh(survey)
    session.add(Response(survey_id=survey.id, answers=[{"question_id": "q01", "choice": 0}], persona_type="emotional_object_solo"))
    session.commit()
    return survey


def test_cron_route_is_hidden_without_a_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "cron_secret", "")
    assert client.get(CRON_PATH).status_code == 404
    assert client.get(CRON_PATH, headers={"Authorization": "Bearer "}).status_code == 404


@pytest.mark.parametrize("header", [None, "Bearer wrong", "the-cron-secret", "Bearer the-cron-secret-x", "Bearer "])
def test_cron_route_rejects_wrong_credentials(client, session, monkeypatch, header):
    monkeypatch.setattr(settings, "cron_secret", "the-cron-secret")
    add_expired_survey(session)
    response = client.get(CRON_PATH, headers={"Authorization": header} if header else {})
    assert response.status_code == 401
    session.expire_all()
    assert len(session.exec(select(Survey)).all()) == 1


def test_cron_route_deletes_expired_data(client, session, monkeypatch):
    monkeypatch.setattr(settings, "cron_secret", "the-cron-secret")
    add_expired_survey(session)
    response = client.get(CRON_PATH, headers={"Authorization": "Bearer the-cron-secret"})
    assert response.status_code == 200
    assert response.json() == {"surveys": 1, "responses": 1}
    session.expire_all()
    assert session.exec(select(Survey)).all() == []
    assert session.exec(select(Response)).all() == []


def test_cron_route_can_safely_run_twice(client, session, monkeypatch):
    """Vercel Cron은 같은 예약을 두 번 호출할 수 있다."""
    monkeypatch.setattr(settings, "cron_secret", "the-cron-secret")
    add_expired_survey(session)
    headers = {"Authorization": "Bearer the-cron-secret"}
    assert client.get(CRON_PATH, headers=headers).json() == {"surveys": 1, "responses": 1}
    assert client.get(CRON_PATH, headers=headers).json() == {"surveys": 0, "responses": 0}


def test_cron_route_prunes_only_old_rate_limit_rows(client, session, monkeypatch):
    monkeypatch.setattr(settings, "cron_secret", "the-cron-secret")
    now = int(utcnow().timestamp())
    session.add(RateLimitHit(bucket="old", window_start=now - RATE_LIMIT_RETENTION_SECONDS - 1000, hits=3))
    session.add(RateLimitHit(bucket="recent", window_start=now - 60, hits=3))
    session.commit()

    assert client.get(CRON_PATH, headers={"Authorization": "Bearer the-cron-secret"}).status_code == 200
    session.expire_all()
    assert [row.bucket for row in session.exec(select(RateLimitHit)).all()] == ["recent"]


def test_cron_configuration_matches_the_route_and_the_free_plan():
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    crons = config["crons"]
    assert len(crons) == 1
    assert crons[0]["path"] == CRON_PATH
    # Hobby 요금제는 하루 한 번만 허용하고, 더 자주 실행하면 배포가 실패한다.
    minute, hour, day, month, weekday = crons[0]["schedule"].split()
    assert minute.isdigit() and hour.isdigit()
    assert (day, month, weekday) == ("*", "*", "*")


def test_functions_run_in_one_region():
    # Hobby 요금제는 한 지역만 허용하고, 더 넣으면 배포가 빌드 전에 실패한다.
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert len(config["regions"]) == 1


# ---------- 배포 파일 ----------


def test_vercel_upload_leaves_out_secrets_tests_and_server_files():
    ignored = (ROOT / ".vercelignore").read_text(encoding="utf-8").split()
    assert {".env", "tests/", "deploy/", "*.db"} <= set(ignored)
    for needed in ("app/", "data/", "app", "data", "requirements.txt"):
        assert needed not in ignored


def test_python_version_is_pinned_for_vercel():
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"


def test_postgres_driver_is_pinned():
    assert re.search(r"^psycopg\[binary\]==\d", (ROOT / "requirements.txt").read_text(encoding="utf-8"), re.M)
