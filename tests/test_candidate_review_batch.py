import copy
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVERS_DIR = PROJECT_ROOT / "servers"
if str(SERVERS_DIR) not in sys.path:
    sys.path.insert(0, str(SERVERS_DIR))

import search_sqlite  # noqa: E402
from servers import app as app_module  # noqa: E402


def _setup_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE daily_memory_candidates (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              date_key TEXT NOT NULL,
              summary_version INTEGER NOT NULL,
              label TEXT NOT NULL,
              evidence TEXT,
              domain TEXT NOT NULL,
              function TEXT NOT NULL,
              primary_mother TEXT NOT NULL,
              secondary_mother TEXT,
              importance INTEGER,
              confidence TEXT,
              source_message_ids_json TEXT,
              status TEXT NOT NULL DEFAULT 'candidate',
              metadata_json TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        rows = [
            (1, "2026-05-17", "ordinary", "profile", "recall", 3),
            (2, "2026-05-18", "sensitive", "health_safety", "soothe_panic", 4),
            (3, "2026-05-19", "ordinary two", "profile", "recall", 2),
            (4, "2026-05-24", "outside", "profile", "recall", 2),
        ]
        conn.executemany(
            """
            INSERT INTO daily_memory_candidates (
                id, date_key, summary_version, label, evidence, domain, function,
                primary_mother, importance, confidence, source_message_ids_json,
                status, metadata_json, created_at
            ) VALUES (?, ?, 7, ?, 'evidence', ?, ?, 'F', ?, 'high', '[10]',
                      'candidate', '{"keep":true}', '2026-05-20T00:00:00Z')
            """,
            rows,
        )
        search_sqlite.ensure_summary_tables(conn.cursor())
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def batch_db(tmp_path, monkeypatch):
    path = tmp_path / "batch.db"
    _setup_db(path)
    monkeypatch.setattr(search_sqlite, "DB_PATH", path)
    monkeypatch.setattr(app_module, "APP_TOKEN", "")
    return path


def _manifest() -> dict:
    return {
        "batch_id": "week-2026-05-17-v1",
        "actor": "manual-weekly-review",
        "scope": {
            "start_date": "2026-05-17",
            "end_date": "2026-05-23",
            "required_current_status": "candidate",
            "expected_count": 3,
        },
        "operations": [
            {
                "candidate_id": 1,
                "status": "merged",
                "reason_code": "covered_by_canonical_memory",
                "merge_target": {"type": "mother_section", "ref": "F.1"},
            },
            {
                "candidate_id": 2,
                "status": "rejected",
                "reason_code": "transient_health_snapshot",
                "manual_override": True,
                "review_note": "Historical state reviewed manually.",
            },
            {"candidate_id": 3, "status": "deferred"},
        ],
    }


def _preview(manifest: dict) -> dict:
    return search_sqlite.preview_memory_candidate_review_batch(**manifest)


def _apply(manifest: dict, digest: str) -> dict:
    return search_sqlite.apply_memory_candidate_review_batch(
        preview_digest=digest,
        **manifest,
    )


def test_preview_is_read_only_deterministic_and_complete(batch_db):
    manifest = _manifest()
    before = batch_db.read_bytes()

    first = _preview(manifest)
    reordered = copy.deepcopy(manifest)
    reordered["operations"].reverse()
    second = _preview(reordered)

    assert first["valid"] is True
    assert first["noop"] is False
    assert first["scope"] == {"matched_count": 3, "expected_count": 3}
    assert first["proposed_counts"] == {"merged": 1, "rejected": 1, "deferred": 1}
    assert first["preview_digest"] == second["preview_digest"]
    assert batch_db.read_bytes() == before


@pytest.mark.parametrize(
    ("mutate", "conflict_code"),
    [
        (lambda value: value["scope"].update(expected_count=4), "SCOPE_COUNT_MISMATCH"),
        (lambda value: value["operations"].pop(), "SCOPE_CANDIDATES_MISSING_FROM_MANIFEST"),
        (lambda value: value["operations"][0].update(candidate_id=4), "CANDIDATE_OUT_OF_SCOPE"),
        (lambda value: value["operations"][0].update(candidate_id=999), "CANDIDATE_NOT_FOUND"),
    ],
)
def test_preview_rejects_scope_and_manifest_conflicts(batch_db, mutate, conflict_code):
    manifest = _manifest()
    mutate(manifest)

    preview = _preview(manifest)

    assert preview["valid"] is False
    assert conflict_code in {item["code"] for item in preview["conflicts"]}


def test_preview_validates_targets_reasons_and_sensitive_rejections(batch_db):
    manifest = _manifest()
    manifest["operations"][0].pop("merge_target")
    manifest["operations"][1].pop("reason_code")
    manifest["operations"][1]["manual_override"] = False
    manifest["operations"][1]["review_note"] = ""

    preview = _preview(manifest)
    errors = {
        error["field"]
        for operation in preview["invalid_operations"]
        for error in operation["errors"]
    }

    assert preview["valid"] is False
    assert {"merge_target", "reason_code", "manual_override", "review_note"} <= errors


def test_apply_updates_only_lifecycle_fields_and_writes_audit(batch_db):
    manifest = _manifest()
    preview = _preview(manifest)
    conn = sqlite3.connect(batch_db)
    conn.row_factory = sqlite3.Row
    before = dict(conn.execute("SELECT * FROM daily_memory_candidates WHERE id = 1").fetchone())
    outside_before = dict(conn.execute("SELECT * FROM daily_memory_candidates WHERE id = 4").fetchone())
    conn.close()

    result = _apply(manifest, preview["preview_digest"])

    assert result["applied"] is True
    assert result["changed_count"] == 3
    assert result["after_counts"] == {"merged": 1, "rejected": 1, "deferred": 1}
    assert result["readback_verified"] is True
    conn = sqlite3.connect(batch_db)
    conn.row_factory = sqlite3.Row
    after = dict(conn.execute("SELECT * FROM daily_memory_candidates WHERE id = 1").fetchone())
    outside_after = dict(conn.execute("SELECT * FROM daily_memory_candidates WHERE id = 4").fetchone())
    batch_count = conn.execute("SELECT COUNT(*) FROM candidate_review_batches").fetchone()[0]
    event_count = conn.execute("SELECT COUNT(*) FROM candidate_review_events").fetchone()[0]
    conn.close()
    assert after["status"] == "merged"
    assert after["updated_at"]
    for field in set(before) - {"status", "updated_at"}:
        assert after[field] == before[field]
    assert outside_after == outside_before
    assert (batch_count, event_count) == (1, 3)


def test_apply_requires_digest_and_is_idempotent(batch_db):
    manifest = _manifest()
    preview = _preview(manifest)
    with pytest.raises(search_sqlite.CandidateReviewBatchError) as mismatch:
        _apply(manifest, "sha256:wrong")
    assert mismatch.value.code == "PREVIEW_DIGEST_MISMATCH"

    _apply(manifest, preview["preview_digest"])
    after_apply = batch_db.read_bytes()
    retry = _apply(manifest, preview["preview_digest"])
    assert retry["noop"] is True
    assert retry["changed_count"] == 0
    assert batch_db.read_bytes() == after_apply


def test_same_batch_id_with_different_manifest_conflicts(batch_db):
    manifest = _manifest()
    preview = _preview(manifest)
    _apply(manifest, preview["preview_digest"])
    manifest["operations"][0]["review_note"] = "Different manifest"

    with pytest.raises(search_sqlite.CandidateReviewBatchError) as conflict:
        _preview(manifest)

    assert conflict.value.code == "BATCH_ID_CONFLICT"


def test_apply_rolls_back_all_rows_and_audit_on_failure(batch_db):
    manifest = _manifest()
    preview = _preview(manifest)
    conn = sqlite3.connect(batch_db)
    conn.execute(
        """
        CREATE TRIGGER fail_candidate_two BEFORE UPDATE ON daily_memory_candidates
        WHEN OLD.id = 2 BEGIN SELECT RAISE(ABORT, 'forced failure'); END
        """
    )
    conn.commit()
    conn.close()

    with pytest.raises(sqlite3.IntegrityError):
        _apply(manifest, preview["preview_digest"])

    conn = sqlite3.connect(batch_db)
    statuses = conn.execute(
        "SELECT status FROM daily_memory_candidates WHERE id IN (1, 2, 3) ORDER BY id"
    ).fetchall()
    audit_count = conn.execute("SELECT COUNT(*) FROM candidate_review_batches").fetchone()[0]
    conn.close()
    assert statuses == [("candidate",), ("candidate",), ("candidate",)]
    assert audit_count == 0


def test_http_apply_returns_structured_conflict(batch_db):
    manifest = _manifest()
    client = TestClient(app_module.app)
    preview = client.post("/memory/candidates/review-batch/preview", json=manifest)
    assert preview.status_code == 200

    response = client.post(
        "/memory/candidates/review-batch/apply",
        json={**manifest, "preview_digest": "sha256:wrong"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PREVIEW_DIGEST_MISMATCH"


def test_week_sized_batch_applies_109_merged_and_43_rejected(tmp_path, monkeypatch):
    path = tmp_path / "week.db"
    _setup_db(path)
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM daily_memory_candidates")
    conn.executemany(
        """
        INSERT INTO daily_memory_candidates (
            id, date_key, summary_version, label, domain, function,
            primary_mother, importance, status, created_at
        ) VALUES (?, '2026-05-20', 1, ?, 'profile', 'recall', 'F', 2,
                  'candidate', '2026-05-20T00:00:00Z')
        """,
        [(candidate_id, f"Candidate {candidate_id}") for candidate_id in range(1, 153)],
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(search_sqlite, "DB_PATH", path)
    operations = [
        {
            "candidate_id": candidate_id,
            "status": "merged",
            "reason_code": "covered_by_canonical_memory",
            "merge_target": {"type": "canonical_topic", "ref": "test.topic"},
        }
        for candidate_id in range(1, 110)
    ]
    operations.extend(
        {
            "candidate_id": candidate_id,
            "status": "rejected",
            "reason_code": "non_durable_duplicate",
        }
        for candidate_id in range(110, 153)
    )
    manifest = {
        "batch_id": "full-week-v1",
        "actor": "test-reviewer",
        "scope": {
            "start_date": "2026-05-17",
            "end_date": "2026-05-23",
            "required_current_status": "candidate",
            "expected_count": 152,
        },
        "operations": operations,
    }

    preview = _preview(manifest)
    result = _apply(manifest, preview["preview_digest"])

    assert preview["valid"] is True
    assert preview["proposed_counts"] == {"merged": 109, "rejected": 43}
    assert result["changed_count"] == 152
    assert result["after_counts"] == {"merged": 109, "rejected": 43}
