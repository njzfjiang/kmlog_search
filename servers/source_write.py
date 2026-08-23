from __future__ import annotations

import difflib
import hashlib
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path


def text_revision(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def read_utf8_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def normalize_fragment(value: str, newline: str) -> str:
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    return normalized.replace("\n", newline)


def unified_text_diff(path: Path, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path),
        )
    )


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp_path.open("wb") as temp_file:
            temp_file.write(text.encode("utf-8"))
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def atomic_replace_with_backup(path: Path, text: str) -> Path:
    backup_dir = path.parent / ".backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_path = backup_dir / f"{path.name}.{timestamp}.bak"
    shutil.copy2(path, backup_path)
    atomic_write_text(path, text)
    return backup_path
