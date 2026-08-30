from datetime import date
from pathlib import Path

from servers.source_write import (
    atomic_replace_with_backup,
    atomic_write_text,
    detect_newline,
    normalize_fragment,
    read_utf8_text,
    text_revision,
    update_frontmatter_last_updated,
    unified_text_diff,
)


def test_text_helpers_preserve_document_newline_style(tmp_path):
    source = tmp_path / "source.md"
    source.write_bytes(b"alpha\r\nbeta\r\n")
    text = read_utf8_text(source)

    assert detect_newline(text) == "\r\n"
    assert normalize_fragment(" one\ntwo ", "\r\n") == "one\r\ntwo"
    assert text_revision(text).startswith("sha256:")
    assert text_revision(text) == text_revision(text)


def test_unified_text_diff_names_source_file(tmp_path):
    source = tmp_path / "source.md"
    diff = unified_text_diff(source, "before\n", "after\n")

    assert f"--- {source}" in diff
    assert f"+++ {source}" in diff
    assert "-before" in diff
    assert "+after" in diff


def test_frontmatter_last_updated_preserves_newlines_and_inserts_missing_field():
    updated = update_frontmatter_last_updated(
        "---\r\nlast updated: 2026-08-23\r\nname: test\r\n---\r\nBody\r\n",
        date(2026, 8, 30),
    )
    inserted = update_frontmatter_last_updated(
        "---\nname: test\n---\nBody\n",
        date(2026, 8, 30),
    )

    assert "last updated: 2026-08-30\r\n" in updated
    assert "\n" not in updated.replace("\r\n", "")
    assert inserted.startswith("---\nlast updated: 2026-08-30\nname: test")


def test_atomic_replace_creates_backup_and_cleans_temp_file(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("before\n", encoding="utf-8", newline="")

    backup = atomic_replace_with_backup(source, "after\n")

    assert source.read_text(encoding="utf-8") == "after\n"
    assert backup.parent == tmp_path / ".backups"
    assert backup.read_text(encoding="utf-8") == "before\n"
    assert not list(tmp_path.glob(".source.md.*.tmp"))


def test_atomic_write_supports_new_files(tmp_path):
    target = tmp_path / "nested" / "generated.json"

    atomic_write_text(target, '{"ok": true}\n')

    assert target.read_text(encoding="utf-8") == '{"ok": true}\n'
    assert not list(target.parent.glob(".generated.json.*.tmp"))
