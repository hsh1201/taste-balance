"""서버로 올릴 압축 파일을 만든다.

사용법 (프로젝트 루트에서):
    python deploy/package.py

.env, DB, 가상환경이 실수로 들어가지 않도록 넣을 것만 지정한다.
"""

import io
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "dist" / "taste-balance.tar.gz"
ARCHIVE_ROOT = "taste-balance"

INCLUDED = ("app", "data", "scripts", "deploy", "requirements.txt", ".env.example", "README.md")
SKIPPED_DIRECTORIES = {"__pycache__"}
SKIPPED_SUFFIXES = {".pyc", ".db"}
# Windows 편집기가 줄바꿈을 CRLF로 바꾸면 리눅스에서 스크립트와 설정이 깨진다.
LF_ONLY_SUFFIXES = {".sh", ".conf", ".service", ".cron"}


def files_to_package(root: Path = ROOT) -> list[Path]:
    files = []
    for name in INCLUDED:
        path = root / name
        candidates = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
        files += [
            file
            for file in candidates
            if not SKIPPED_DIRECTORIES & set(file.relative_to(root).parts) and file.suffix not in SKIPPED_SUFFIXES
        ]
    return files


def build_archive(output: Path = OUTPUT, root: Path = ROOT) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for file in files_to_package(root):
            data = file.read_bytes()
            if file.suffix in LF_ONLY_SUFFIXES:
                data = data.replace(b"\r\n", b"\n")
            info = tarfile.TarInfo(f"{ARCHIVE_ROOT}/{file.relative_to(root).as_posix()}")
            info.size = len(data)
            info.mode = 0o755 if file.suffix == ".sh" else 0o644
            archive.addfile(info, io.BytesIO(data))
    return output


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    output = build_archive()
    size = output.stat().st_size / 1024
    print(f"만들었습니다: {output} ({size:.0f} KB, 파일 {len(files_to_package())}개)")


if __name__ == "__main__":
    main()
