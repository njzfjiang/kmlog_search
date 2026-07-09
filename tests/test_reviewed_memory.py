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
        metadata_json={"note": "human edited"},
    )

    assert item["title"] == "Reviewed title"
    assert item["content"] == "Curated reviewed memory."
    assert item["domain"] == "profile"
    assert item["function"] == "boot_core"
    assert item["explicitness"] == "explicit_user_said"
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


def test_update_memory_candidate_status(tmp_path, monkeypatch):
    db_path = tmp_path / "chat_search.db"
    _setup_reviewed_memory_db(db_path)
    monkeypatch.setattr(search_sqlite, "DB_PATH", db_path)

    candidate = search_sqlite.update_memory_candidate_status(10, "accepted")

    assert candidate["id"] == 10
    assert candidate["status"] == "accepted"
    assert search_sqlite.update_memory_candidate_status(999, "rejected") is None
