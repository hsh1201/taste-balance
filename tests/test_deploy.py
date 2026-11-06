import re
import tarfile
from pathlib import Path

import pytest

from app.services import ai_comment
from deploy.package import ARCHIVE_ROOT, LF_ONLY_SUFFIXES, build_archive, files_to_package

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
SERVER_FILES = [path for path in sorted(DEPLOY.iterdir()) if path.suffix in LF_ONLY_SUFFIXES]


def read(name: str) -> str:
    return (DEPLOY / name).read_text(encoding="utf-8")


@pytest.fixture
def archive_names(tmp_path) -> set[str]:
    with tarfile.open(build_archive(tmp_path / "out.tar.gz")) as archive:
        return set(archive.getnames())


def test_archive_has_what_the_server_needs(archive_names):
    for name in (
        "app/main.py",
        "app/templates/result.html",
        "app/static/js/ai-comment.js",
        "data/gifts.json",
        "scripts/cleanup.py",
        "requirements.txt",
        "deploy/setup.sh",
        "deploy/taste-balance.service",
        "deploy/nginx-proxy.conf",
    ):
        assert f"{ARCHIVE_ROOT}/{name}" in archive_names


def test_archive_never_contains_secrets_or_local_data(archive_names):
    for name in archive_names:
        parts = Path(name).parts
        assert Path(name).name != ".env"
        assert not name.endswith((".db", ".db-wal", ".db-shm", ".pyc", ".pem", ".key"))
        assert not {"venv", "tests", "dist", "__pycache__", ".pytest_cache"} & set(parts)


def test_secret_files_are_left_out_even_when_they_exist_nearby(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("x", encoding="utf-8")
    (tmp_path / "app" / "__pycache__").mkdir()
    (tmp_path / "app" / "__pycache__" / "main.pyc").write_text("x", encoding="utf-8")
    (tmp_path / "app" / "local.db").write_text("x", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET", encoding="utf-8")
    for name in ("data", "scripts", "deploy"):
        (tmp_path / name).mkdir()
    (tmp_path / "requirements.txt").write_text("x", encoding="utf-8")
    (tmp_path / ".env.example").write_text("x", encoding="utf-8")
    (tmp_path / "README.md").write_text("x", encoding="utf-8")

    names = {path.relative_to(tmp_path).as_posix() for path in files_to_package(tmp_path)}
    assert names == {"app/main.py", "requirements.txt", ".env.example", "README.md"}


def test_archive_converts_windows_line_endings(tmp_path):
    for name in ("app", "data", "scripts", "deploy"):
        (tmp_path / name).mkdir()
    (tmp_path / "deploy" / "setup.sh").write_bytes(b"#!/bin/bash\r\necho hi\r\n")
    (tmp_path / "requirements.txt").write_text("x", encoding="utf-8")
    (tmp_path / ".env.example").write_text("x", encoding="utf-8")
    (tmp_path / "README.md").write_text("x", encoding="utf-8")

    with tarfile.open(build_archive(tmp_path / "out.tar.gz", tmp_path)) as archive:
        member = archive.getmember(f"{ARCHIVE_ROOT}/deploy/setup.sh")
        assert archive.extractfile(member).read() == b"#!/bin/bash\necho hi\n"
        assert member.mode == 0o755


@pytest.mark.parametrize("path", SERVER_FILES, ids=lambda path: path.name)
def test_server_files_use_unix_line_endings(path):
    assert b"\r" not in path.read_bytes()


def test_service_listens_only_on_the_loopback_with_one_worker():
    service = read("taste-balance.service")
    assert "--host 127.0.0.1" in service
    assert "--workers 1" in service
    assert "--forwarded-allow-ips 127.0.0.1" in service
    assert "User=tastebalance" in service


def test_secret_links_stay_out_of_the_logs():
    assert "--no-access-log" in read("taste-balance.service")
    proxy = read("nginx-proxy.conf")
    private = re.search(r"location ~ \^/\(r\|g\)/ \{(.*?)\}", proxy, re.S).group(1)
    assert "access_log off;" in private
    assert " crit;" in private
    redirect_server = read("nginx-https.conf").split("server {")[1]
    assert "access_log off;" in redirect_server


def test_visitor_cannot_fake_an_ip_to_dodge_the_rate_limit():
    proxy = read("nginx-proxy.conf")
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in proxy
    assert "proxy_add_x_forwarded_for" not in proxy


def test_nginx_waits_longer_than_the_slowest_ai_comment():
    seconds = int(re.search(r"proxy_read_timeout (\d+)s;", read("nginx-proxy.conf")).group(1))
    assert seconds > ai_comment.REQUEST_TIMEOUT_SECONDS * (ai_comment.MAX_RETRIES + 1)


def test_browser_waits_longer_than_nginx():
    script = (ROOT / "app" / "static" / "js" / "ai-comment.js").read_text(encoding="utf-8")
    browser_ms = int(re.search(r"TIMEOUT_MS = (\d+)", script).group(1))
    seconds = int(re.search(r"proxy_read_timeout (\d+)s;", read("nginx-proxy.conf")).group(1))
    assert browser_ms >= seconds * 1000


def test_setup_keeps_the_key_out_of_arguments_and_output():
    setup = read("setup.sh")
    assert "read -rsp" in setup
    assert "set -x" not in setup
    assert not re.search(r"echo .*(SECRET_KEY|LLM_KEY)", setup)


def test_setup_keeps_existing_settings_when_run_again():
    setup = read("setup.sh")
    assert 'if [[ -f "$ENV_FILE" ]]; then' in setup
    assert "chmod 640" in setup
    assert "DATABASE_URL=sqlite:///${DATA_DIR}/taste_balance.db" in setup


def test_cleanup_job_runs_as_the_service_user():
    assert re.search(r"^0 4 \* \* \* tastebalance /opt/taste-balance/venv/bin/python /opt/taste-balance/scripts/cleanup\.py", read("taste-balance-cleanup.cron"), re.M)
    assert read("taste-balance-cleanup.cron").endswith("\n")


def test_guide_tells_the_reader_to_set_a_spending_limit():
    assert "지출 한도" in read("DEPLOY.md")
