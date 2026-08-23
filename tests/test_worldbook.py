import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from servers import worldbook
from servers import app as app_module


def _entry(entry_id: str, name: str, content: str) -> dict:
    return {
        "id": entry_id,
        "name": name,
        "enabled": True,
        "priority": 10,
        "position": "AFTER_SYSTEM_PROMPT",
        "content": content,
        "injectDepth": 2,
        "role": "USER",
        "keywords": [name],
        "useRegex": False,
        "caseSensitive": False,
        "scanDepth": 2,
        "constantActive": False,
    }


def _book(book_id: str, name: str, entries: list[dict]) -> dict:
    return {
        "version": 1,
        "type": "lorebook",
        "data": {
            "id": book_id,
            "name": name,
            "description": name,
            "enabled": True,
            "entries": entries,
        },
    }


def _write_book(path, document):
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="",
    )


def _setup_books(tmp_path, monkeypatch):
    sources = (
        "memory_system.json",
        "Expansion_Pack_v2.json",
        "Recent_Updates.json",
    )
    _write_book(tmp_path / sources[0], _book("base", "Base", [_entry("a", "A", "alpha")]))
    _write_book(tmp_path / sources[1], _book("expansion", "Expansion", [_entry("b", "B", "beta")]))
    _write_book(tmp_path / sources[2], _book("recent", "Recent", [_entry("c", "C", "gamma")]))
    monkeypatch.setattr(worldbook, "DEFAULT_WORLDBOOK_DIR", tmp_path)
    monkeypatch.delenv("KMLOG_WORLDBOOK_DIR", raising=False)
    monkeypatch.delenv("KMLOG_WORLDBOOK_SOURCES", raising=False)
    monkeypatch.delenv("KMLOG_WORLDBOOK_UPDATE_FILE", raising=False)
    monkeypatch.delenv("KMLOG_WORLDBOOK_MERGED_FILE", raising=False)
    return tmp_path / "Recent_Updates.json"


def test_worldbook_merge_preserves_source_order(tmp_path, monkeypatch):
    _setup_books(tmp_path, monkeypatch)

    result = worldbook.rebuild_merged_worldbook()
    merged = json.loads((tmp_path / "World_Book_Merged.json").read_text(encoding="utf-8"))

    assert result["entry_count"] == 3
    assert [item["file"] for item in result["sources"]] == list(
        worldbook.DEFAULT_SOURCE_FILES
    )
    assert [entry["id"] for entry in merged["data"]["entries"]] == ["a", "b", "c"]

    source = worldbook.get_worldbook_source_info()
    assert [item["revision"][:7] for item in source["sources"]] == [
        "sha256:",
        "sha256:",
        "sha256:",
    ]
    assert [item["writable"] for item in source["sources"]] == [False, False, True]
    assert all(item["updated_at"] for item in source["sources"])


def test_worldbook_preview_does_not_write_source(tmp_path, monkeypatch):
    recent_path = _setup_books(tmp_path, monkeypatch)
    before = recent_path.read_text(encoding="utf-8")
    source = worldbook.get_worldbook_source_info()

    preview = worldbook.preview_worldbook_update(
        source["revision"],
        [{"op": "patch_entry", "id": "c", "changes": {"content": "updated"}}],
    )

    assert preview["valid"] is True
    assert preview["changed_entry_ids"] == ["c"]
    assert '-        "content": "gamma"' in preview["diff"]
    assert '+        "content": "updated"' in preview["diff"]
    assert recent_path.read_text(encoding="utf-8") == before


def test_worldbook_noop_preview_and_apply_do_not_write_files(tmp_path, monkeypatch):
    recent_path = _setup_books(tmp_path, monkeypatch)
    worldbook.rebuild_merged_worldbook()
    merged_path = tmp_path / "World_Book_Merged.json"
    source_before = recent_path.read_bytes()
    source_mtime = recent_path.stat().st_mtime_ns
    merged_before = merged_path.read_bytes()
    merged_mtime = merged_path.stat().st_mtime_ns
    source = worldbook.get_worldbook_source_info()
    operations = [{"op": "set_enabled", "id": "c", "enabled": True}]

    preview = worldbook.preview_worldbook_update(source["revision"], operations)
    applied = worldbook.apply_worldbook_update(source["revision"], operations)

    assert preview["noop"] is True
    assert preview["changed_entry_ids"] == []
    assert preview["diff"] == ""
    assert applied["noop"] is True
    assert applied["applied"] is False
    assert applied["backup_file"] is None
    assert applied["merged"] is None
    assert recent_path.read_bytes() == source_before
    assert recent_path.stat().st_mtime_ns == source_mtime
    assert merged_path.read_bytes() == merged_before
    assert merged_path.stat().st_mtime_ns == merged_mtime
    assert not (tmp_path / ".backups").exists()


def test_worldbook_apply_backs_up_source_and_rebuilds_merge(tmp_path, monkeypatch):
    recent_path = _setup_books(tmp_path, monkeypatch)
    source = worldbook.get_worldbook_source_info()

    result = worldbook.apply_worldbook_update(
        source["revision"],
        [
            {"op": "set_enabled", "id": "c", "enabled": False},
            {"op": "create_entry", "entry": _entry("d", "D", "delta")},
        ],
        actor="test",
    )

    recent = json.loads(recent_path.read_text(encoding="utf-8"))
    merged = json.loads((tmp_path / "World_Book_Merged.json").read_text(encoding="utf-8"))
    assert result["applied"] is True
    assert result["actor"] == "test"
    assert result["merged"]["entry_count"] == 4
    backup = json.loads(Path(result["backup_file"]).read_text(encoding="utf-8"))
    assert backup["data"]["entries"][0]["enabled"] is True
    assert recent["data"]["entries"][0]["enabled"] is False
    assert [entry["id"] for entry in merged["data"]["entries"]] == ["a", "b", "c", "d"]


def test_worldbook_rejects_stale_revision_and_cross_source_duplicates(
    tmp_path, monkeypatch
):
    recent_path = _setup_books(tmp_path, monkeypatch)
    source = worldbook.get_worldbook_source_info()
    recent = json.loads(recent_path.read_text(encoding="utf-8"))
    recent["data"]["description"] = "external edit"
    _write_book(recent_path, recent)

    with pytest.raises(worldbook.WorldBookRevisionConflictError) as conflict:
        worldbook.preview_worldbook_update(
            source["revision"],
            [{"op": "set_enabled", "id": "c", "enabled": False}],
        )
    assert conflict.value.code == "REVISION_CONFLICT"
    assert conflict.value.expected_revision == source["revision"]
    assert conflict.value.current_revision != source["revision"]

    recent["data"]["entries"].append(_entry("a", "Duplicate A", "duplicate"))
    _write_book(recent_path, recent)
    with pytest.raises(ValueError, match="Duplicate World Book entry id"):
        worldbook.rebuild_merged_worldbook()


def test_worldbook_searches_name_keywords_and_content(tmp_path, monkeypatch):
    _setup_books(tmp_path, monkeypatch)

    result = worldbook.list_worldbook_entries(q="beta", enabled=True)

    assert result["total_entries"] == 3
    assert [entry["id"] for entry in result["results"]] == ["b"]
    assert result["results"][0]["source_file"] == "Expansion_Pack_v2.json"
    assert result["results"][0]["source_lorebook_id"] == "expansion"
    assert result["results"][0]["writable"] is False

    writable = worldbook.list_worldbook_entries(q="gamma")["results"][0]
    assert writable["source_file"] == "Recent_Updates.json"
    assert writable["source_lorebook_id"] == "recent"
    assert writable["writable"] is True


def test_worldbook_http_preview_requires_write_token(tmp_path, monkeypatch):
    _setup_books(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module, "APP_TOKEN", "")
    monkeypatch.setattr(app_module, "WORLDBOOK_WRITE_TOKEN", "write-secret")
    client = TestClient(app_module.app)
    source = client.get("/worldbook/source").json()
    payload = {
        "expected_revision": source["revision"],
        "operations": [
            {"op": "patch_entry", "id": "c", "changes": {"content": "updated"}}
        ],
    }

    unauthorized = client.post("/worldbook/updates/preview", json=payload)
    preview = client.post(
        "/worldbook/updates/preview",
        json=payload,
        headers={"X-API-Key": "write-secret"},
    )

    assert unauthorized.status_code == 401
    assert preview.status_code == 200
    assert preview.json()["changed_entry_ids"] == ["c"]


def test_worldbook_http_returns_conflict_for_stale_revision(tmp_path, monkeypatch):
    recent_path = _setup_books(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module, "APP_TOKEN", "")
    monkeypatch.setattr(app_module, "WORLDBOOK_WRITE_TOKEN", "")
    client = TestClient(app_module.app)
    source = client.get("/worldbook/source").json()
    recent_path.write_text(
        recent_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
        newline="",
    )

    response = client.post(
        "/worldbook/updates/preview",
        json={
            "expected_revision": source["revision"],
            "operations": [
                {"op": "set_enabled", "id": "c", "enabled": False}
            ],
        },
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "REVISION_CONFLICT"
    assert detail["expected_revision"] == source["revision"]
    assert detail["current_revision"] != source["revision"]
