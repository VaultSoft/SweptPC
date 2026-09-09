from __future__ import annotations

import json
import re
import sys
from pathlib import Path

APP_NAME = "SweptPC"
VERSION_FILENAME = "version.json"
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def app_base_path() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def version_file_path(base_path: str | Path | None = None) -> Path:
    base = Path(base_path) if base_path is not None else app_base_path()
    return base / VERSION_FILENAME


def read_app_version(base_path: str | Path | None = None) -> str:
    path = version_file_path(base_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    version = str(data.get("version", "")).strip()
    if not _SEMVER_RE.fullmatch(version):
        raise RuntimeError(f"Invalid {VERSION_FILENAME} version: {version!r}")
    return version


APP_VERSION = read_app_version()


def package_zip_filename(version: str | None = None) -> str:
    return f"{APP_NAME}-v{version or APP_VERSION}-portable.zip"
