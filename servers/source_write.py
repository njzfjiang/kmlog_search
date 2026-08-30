from __future__ import annotations

import difflib
import hashlib
import os
import re
import shutil
import uuid
from datetime import date, datetime, timezone
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


def current_utc_date() -> date:
    return datetime.now(timezone.utc).date()


def update_frontmatter_last_updated(
    text: str,
    updated_on: date | None = None,
) -> str:
    newline = detect_newline(text)
    frontmatter = re.match(
        r"\A---\r?\n(?P<body>.*?)(?P<closing>^---[ \t]*(?:\r?\n|$))",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if frontmatter is None:
        raise ValueError("Markdown source requires YAML frontmatter")

    value = (updated_on or current_utc_date()).isoformat()
    body = frontmatter.group("body")
    pattern = re.compile(r"^last updated:[^\r\n]*", re.MULTILINE)
    if pattern.search(body):
        updated_body = pattern.sub(f"last updated: {value}", body, count=1)
    else:
        updated_body = f"last updated: {value}{newline}{body}"
    return text[: frontmatter.start("body")] + updated_body + text[frontmatter.end("body") :]


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
