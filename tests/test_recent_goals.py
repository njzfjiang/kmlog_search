from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from servers import app as app_module
from servers import recent_goals


def _item(item_id: str = "J-TEST-001", **changes) -> dict:
    item = {
        "id": item_id,
        "title": "Test goal",
        "body": "- Keep the source readable.",
        "owner": "Shared",
        "area": "infra",
        "status": "active",
        "created_at": "2026-08-23",
        "review_on": "2999-09-15",
    }
    item.update(changes)
    return item


def _setup_source(tmp_path, monkeypatch, items=None) -> Path:
    path = tmp_path / "Recent Goals(Current).md"
    document = {
        "preamble": "---\nlast updated: 2026-08-23\n---\n# J. Recent Goals/TODOs",
        "items": items or [_item()],
    }
    path.write_text(
        recent_goals.serialize_j_document(document),
        encoding="utf-8",
        newline="",
    )
    monkeypatch.setattr(recent_goals, "DEFAULT_J_SOURCE_FILE", path)
    monkeypatch.delenv("KMLOG_J_SOURCE_FILE", raising=False)
    return path


def test_j_source_parses_structured_items_and_cleanup(tmp_path, monkeypatch):
    _setup_source(
        tmp_path,
        monkeypatch,
        [
            _item(review_on="2026-08-23"),
            _item(
                "J-TEST-002",
                status="archived",
                archived_at="2026-08-23",
                archive_reason="completed",
            ),
        ],
    )

    source = recent_goals.get_j_source_info()

    assert source["item_count"] == 2
    assert source["active_count"] == 1
    assert source["archived_count"] == 1
    assert source["cleanup_candidates"] == [
        {
            "id": "J-TEST-001",
            "reason": "review_due",
            "effective_date": "2026-08-23",
        }
    ]


def test_j_requires_created_at_and_lifecycle_date():
    with pytest.raises(recent_goals.JValidationError, match="created_at"):
        recent_goals.serialize_j_document(
            {"preamble": "# J", "items": [_item(created_at=None)]}
        )
    with pytest.raises(recent_goals.JValidationError, match="expires_at or review_on"):
        recent_goals.serialize_j_document(
            {"preamble": "# J", "items": [_item(review_on=None)]}
        )


def test_j_preview_and_noop_apply_do_not_write(tmp_path, monkeypatch):
    path = _setup_source(tmp_path, monkeypatch)
    source = recent_goals.get_j_source_info()
    before = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns
    operations = [
        {"op": "patch", "id": "J-TEST-001", "changes": {"title": "Test goal"}}
    ]

    preview = recent_goals.preview_j_update(source["revision"], operations)
    applied = recent_goals.apply_j_update(source["revision"], operations)

    assert preview["noop"] is True
    assert preview["changed_item_ids"] == []
    assert preview["diff"] == ""
    assert applied["noop"] is True
    assert applied["applied"] is False
    assert applied["backup_file"] is None
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == before_mtime
    assert not (tmp_path / ".backups").exists()


def test_j_apply_create_patch_archive_backs_up_and_reads_back(tmp_path, monkeypatch):
    path = _setup_source(tmp_path, monkeypatch)
    source = recent_goals.get_j_source_info()
    created = _item("J-TEST-002", title="Second goal")

    applied = recent_goals.apply_j_update(
        source["revision"],
        [
            {"op": "patch", "id": "J-TEST-001", "changes": {"body": "Updated."}},
            {"op": "create", "item": created},
            {
                "op": "archive",
                "id": "J-TEST-001",
                "reason": "completed",
                "archived_at": "2026-08-23",
            },
        ],
        actor="test",
    )

    readback = recent_goals.get_j_source_info()
    backup = Path(applied["backup_file"])
    assert applied["readback_verified"] is True
    assert applied["changed_item_ids"] == ["J-TEST-002", "J-TEST-001"]
    assert applied["actor"] == "test"
    assert backup.exists()
    assert "Updated." not in backup.read_text(encoding="utf-8")
    assert readback["active_items"][0]["id"] == "J-TEST-002"
    assert readback["archived_items"][0]["archive_reason"] == "completed"
    assert "## Active" in path.read_text(encoding="utf-8")
    assert "## Archive" in path.read_text(encoding="utf-8")


def test_j_changed_preview_and_apply_refresh_last_updated(tmp_path, monkeypatch):
    path = _setup_source(tmp_path, monkeypatch)
    update_date = recent_goals.update_frontmatter_last_updated
    monkeypatch.setattr(
        recent_goals,
        "update_frontmatter_last_updated",
        lambda text: update_date(text, date(2026, 8, 30)),
    )
    source = recent_goals.get_j_source_info()
    operations = [
        {"op": "patch", "id": "J-TEST-001", "changes": {"body": "Updated."}}
    ]

    preview = recent_goals.preview_j_update(source["revision"], operations)
    applied = recent_goals.apply_j_update(source["revision"], operations)

    assert "+last updated: 2026-08-30" in preview["diff"]
    assert applied["applied"] is True
    assert "last updated: 2026-08-30" in path.read_text(encoding="utf-8")


def test_j_rejects_stale_revision_and_unsupported_mutations(tmp_path, monkeypatch):
    path = _setup_source(tmp_path, monkeypatch)
    source = recent_goals.get_j_source_info()
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(recent_goals.JRevisionConflictError) as conflict:
        recent_goals.preview_j_update(
            source["revision"],
            [{"op": "patch", "id": "J-TEST-001", "changes": {"title": "New"}}],
        )
    assert conflict.value.current_revision != source["revision"]

    current = recent_goals.get_j_source_info()
    with pytest.raises(recent_goals.JValidationError, match="status is immutable"):
        recent_goals.preview_j_update(
            current["revision"],
            [{"op": "patch", "id": "J-TEST-001", "changes": {"status": "archived"}}],
        )
    with pytest.raises(recent_goals.JValidationError, match="unsupported operation"):
        recent_goals.preview_j_update(
            current["revision"],
            [{"op": "delete", "id": "J-TEST-001"}],
        )


def test_j_http_write_auth_and_structured_conflict(tmp_path, monkeypatch):
    path = _setup_source(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module, "APP_TOKEN", "")
    monkeypatch.setattr(app_module, "J_WRITE_TOKEN", "write-secret")
    client = TestClient(app_module.app)
    source = client.get("/j/source").json()
    payload = {
        "expected_revision": source["revision"],
        "operations": [
            {"op": "patch", "id": "J-TEST-001", "changes": {"title": "Updated"}}
        ],
    }

    unauthorized = client.post("/j/updates/preview", json=payload)
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    conflict = client.post(
        "/j/updates/preview",
        json=payload,
        headers={"X-API-Key": "write-secret"},
    )

    assert unauthorized.status_code == 401
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "REVISION_CONFLICT"
    assert conflict.json()["detail"]["current_revision"] != source["revision"]


def test_j_http_returns_structured_validation_detail(tmp_path, monkeypatch):
    _setup_source(tmp_path, monkeypatch)
    monkeypatch.setattr(app_module, "APP_TOKEN", "")
    monkeypatch.setattr(app_module, "J_WRITE_TOKEN", "")
    client = TestClient(app_module.app)
    source = client.get("/j/source").json()

    response = client.post(
        "/j/updates/preview",
        json={
            "expected_revision": source["revision"],
            "operations": [
                {
                    "op": "patch",
                    "id": "J-TEST-001",
                    "changes": {"created_at": "2026-08-24"},
                }
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "VALIDATION_ERROR",
        "operation_index": 0,
        "item_id": "J-TEST-001",
        "field": "created_at",
        "message": "created_at is immutable",
    }


@pytest.mark.parametrize(
    ("operation", "field", "message"),
    [
        (
            {
                "op": "create",
                "item": {
                    "id": "J-TEST-002",
                    "title": "Missing lifecycle",
                    "body": "Body",
                    "owner": "Shared",
                    "area": "infra",
                    "status": "active",
                    "created_at": "2026-08-23",
                },
            },
            "expires_at_or_review_on",
            "expires_at or review_on is required",
        ),
        (
            {"op": "archive", "id": "J-TEST-001", "reason": "completed"},
            "archived_at",
            "archive requires exactly op, id, reason, and archived_at",
        ),
    ],
)
def test_j_validation_detail_identifies_field(
    tmp_path, monkeypatch, operation, field, message
):
    _setup_source(tmp_path, monkeypatch)
    source = recent_goals.get_j_source_info()

    with pytest.raises(recent_goals.JValidationError) as error:
        recent_goals.preview_j_update(source["revision"], [operation])

    assert error.value.operation_index == 0
    assert error.value.field == field
    assert str(error.value) == message
