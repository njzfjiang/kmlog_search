import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVERS_DIR = PROJECT_ROOT / "servers"
if str(SERVERS_DIR) not in sys.path:
    sys.path.insert(0, str(SERVERS_DIR))

import search_sqlite  # noqa: E402


def _setup_reviewed_memory_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                role TEXT,
                content TEXT,
                conversation_title TEXT,
                conversation_id TEXT,
                message_id TEXT UNIQUE,
                kind TEXT DEFAULT 'chat'
            )
            """
        )
        cursor.execute(
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
        cursor.executemany(
            """
            INSERT INTO messages (
                id,
                timestamp,
                role,
                content,
                conversation_title,
                conversation_id,
                message_id,
                kind
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    "2026-05-11T10:00:00-04:00",
                    "user",
                    "I explicitly said this matters.",
                    "Memory test",
                    "conv-1",
                    "msg-uuid-1",
                    "chat",
                ),
                (
                    2,
                    "2026-05-11T10:01:00-04:00",
                    "assistant",
                    "Assistant interpreted the preference.",
                    "Memory test",
                    "conv-1",
                    "msg-uuid-2",
                    "chat",
                ),
            ],
        )
        cursor.execute(
            """
            INSERT INTO daily_memory_candidates (
                id,
                date_key,
                summary_version,
                label,
                evidence,
                domain,
                function,
                primary_mother,
                secondary_mother,
                importance,
                confidence,
                source_message_ids_json,
                status,
                metadata_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                10,
                "2026-05-11",
                1,
                "Candidate title",
                "Candidate evidence",
                "profile",
                "boot_core",
                "A",
                "B",
                4,
                "high",
                "[1, 2]",
                "candidate",
                None,
                "2026-05-11T12:00:00-04:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_promote_memory_candidate_creates_reviewed_item_and_sources(tmp_path, monkeypatch):
    db_path = tmp_path / "chat_search.db"
    _setup_reviewed_memory_db(db_path)
    monkeypatch.setattr(search_sqlite, "DB_PATH", db_path)

    item = search_sqlite.promote_memory_candidate(
        [10],
        title="Reviewed title",
        content="Curated reviewed memory.",
        explicitness="explicit_user_said",
        topic_key="comfort.thunder",
        layer_role="retrieval_summary",
        canonical_ref="mother:F.4.4",
        review_after="2026-08-01T00:00:00Z",
        metadata_json={"note": "human edited"},
    )

    assert item["title"] == "Reviewed title"
    assert item["content"] == "Curated reviewed memory."
    assert item["domain"] == "profile"
    assert item["function"] == "boot_core"
    assert item["explicitness"] == "explicit_user_said"
    assert item["topic_key"] == "comfort.thunder"
    assert item["layer_role"] == "retrieval_summary"
    assert item["canonical_ref"] == "mother:F.4.4"
    assert item["review_after"] == "2026-08-01T00:00:00Z"
    assert item["source_candidate_ids_json"] == "[10]"
    assert item["source_message_ids_json"] == "[1, 2]"
    assert [source["source_role"] for source in item["sources"]] == [
        "candidate",
        "message",
        "message",
    ]
    assert item["sources"][1]["message_id"] == "msg-uuid-1"

    candidates = search_sqlite.list_daily_memory_candidates(status="promoted")
    assert [candidate["id"] for candidate in candidates] == [10]

    reviewed = search_sqlite.list_reviewed_memory_items(
        topic_key="comfort.thunder",
        layer_role="retrieval_summary",
        canonical_ref="mother:F.4.4",
        q="Curated",
        include_sources=True,
    )
    assert [memory["id"] for memory in reviewed] == [item["id"]]
    assert len(reviewed[0]["sources"]) == 3

    by_pk = search_sqlite.get_reviewed_memory_by_message(message_pk=1)
    assert by_pk["message"]["message_id"] == "msg-uuid-1"
    assert [memory["id"] for memory in by_pk["results"]] == [item["id"]]

    by_uuid = search_sqlite.get_reviewed_memory_by_message(message_id="msg-uuid-2")
    assert by_uuid["message"]["id"] == 2
    assert [memory["id"] for memory in by_uuid["results"]] == [item["id"]]
    assert by_uuid["results"][0]["topic_key"] == "comfort.thunder"


def test_reviewed_memory_schema_migrates_existing_table(tmp_path, monkeypatch):
    db_path = tmp_path / "chat_search.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE reviewed_memory_items (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL,
              content TEXT NOT NULL,
              evidence TEXT,
              domain TEXT NOT NULL,
              function TEXT NOT NULL,
              primary_mother TEXT,
              secondary_mother TEXT,
              importance INTEGER DEFAULT 3,
              confidence TEXT,
              explicitness TEXT,
              status TEXT NOT NULL DEFAULT 'active',
              source_candidate_ids_json TEXT,
              source_message_ids_json TEXT,
              reviewer TEXT DEFAULT 'human',
              reviewed_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              expires_at TEXT,
              superseded_by_item_id INTEGER,
              metadata_json TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO reviewed_memory_items (
                title, content, domain, function, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("Existing", "Existing content", "profile", "boot_core", "now", "now"),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(search_sqlite, "DB_PATH", db_path)
    items = search_sqlite.list_reviewed_memory_items()

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(reviewed_memory_items)")}
        index_columns = [
            row[2]
            for row in conn.execute("PRAGMA index_info(idx_reviewed_memory_status_topic_key)")
        ]
        migrated = conn.execute(
            """
            SELECT topic_key, layer_role, canonical_ref, review_after
            FROM reviewed_memory_items
            WHERE title = 'Existing'
            """
        ).fetchone()
    finally:
        conn.close()

    assert columns["topic_key"] == "TEXT"
    assert columns["layer_role"] == "TEXT"
    assert columns["canonical_ref"] == "TEXT"
    assert columns["review_after"] == "TIMESTAMP"
    assert index_columns == ["status", "topic_key"]
    assert migrated == (None, None, None, None)
    assert items[0]["topic_key"] is None


def test_promote_memory_candidate_old_caller_omits_new_fields(tmp_path, monkeypatch):
    db_path = tmp_path / "chat_search.db"
    _setup_reviewed_memory_db(db_path)
    monkeypatch.setattr(search_sqlite, "DB_PATH", db_path)

    item = search_sqlite.promote_memory_candidate([10])

    assert item["topic_key"] is None
    assert item["layer_role"] is None
    assert item["canonical_ref"] is None
    assert item["review_after"] is None


def test_update_memory_candidate_status(tmp_path, monkeypatch):
    db_path = tmp_path / "chat_search.db"
    _setup_reviewed_memory_db(db_path)
    monkeypatch.setattr(search_sqlite, "DB_PATH", db_path)

    candidate = search_sqlite.update_memory_candidate_status(10, "accepted")

    assert candidate["id"] == 10
    assert candidate["status"] == "accepted"
    assert search_sqlite.update_memory_candidate_status(999, "rejected") is None
