import argparse
import json
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUTS = [
    PROJECT_ROOT / "chat_data" / "cleaned_chats.jsonl",
    PROJECT_ROOT / "chat_data" / "chatgpt_incremental_deduped.jsonl",
]
DEFAULT_OUTPUT = PROJECT_ROOT / "chat_data" / "chat_search.db"
REQUIRED_FIELDS = (
    "timestamp",
    "role",
    "content",
    "conversation_title",
    "conversation_id",
    "message_id",
)


def iter_jsonl_messages(paths):
    for path in paths:
        if not path.exists():
            print(f"WARNING: input file not found, skipping: {path}")
            continue

        with path.open("r", encoding="utf-8") as f:
            for line_number, line in enumerate(f, 1):
                if not line.strip():
                    continue

                row = json.loads(line)
                missing = [field for field in REQUIRED_FIELDS if field not in row]
                if missing:
                    raise ValueError(f"{path}:{line_number} missing fields: {', '.join(missing)}")

                yield path, line_number, row


def build_sqlite_fts(input_files=DEFAULT_INPUTS, output_file=DEFAULT_OUTPUT):
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    seen_message_ids = set()
    rows = []
    duplicate_rows = 0

    for _path, _line_number, row in iter_jsonl_messages([Path(p) for p in input_files]):
        message_id = row["message_id"]
        if message_id in seen_message_ids:
            duplicate_rows += 1
            continue

        seen_message_ids.add(message_id)
        rows.append(tuple(row[field] for field in REQUIRED_FIELDS) + (row.get("kind") or "chat",))

    if not rows:
        raise RuntimeError("No messages found to import.")

    conn = sqlite3.connect(output_file)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")

        cursor.execute("DROP TABLE IF EXISTS messages_fts")
        cursor.execute("DROP TABLE IF EXISTS messages")

        cursor.execute("""
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
        """)

        cursor.execute("""
            CREATE VIRTUAL TABLE messages_fts USING fts5(
                content,
                conversation_title,
                content=messages,
                content_rowid=id,
                tokenize='unicode61'
            )
        """)

        cursor.executemany("""
            INSERT INTO messages (
                timestamp,
                role,
                content,
                conversation_title,
                conversation_id,
                message_id,
                kind
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, rows)

        cursor.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        cursor.execute("CREATE INDEX messages_timestamp_idx ON messages(timestamp)")
        cursor.execute("CREATE INDEX messages_role_idx ON messages(role)")
        cursor.execute("CREATE INDEX messages_conversation_idx ON messages(conversation_id)")
        cursor.execute("CREATE INDEX messages_kind_idx ON messages(kind)")

        conn.commit()
    finally:
        conn.close()

    print("SQLite FTS database built.")
    print(f"  Output: {output_file}")
    print(f"  Imported rows: {len(rows)}")
    print(f"  Skipped duplicate message_id rows: {duplicate_rows}")


def main():
    parser = argparse.ArgumentParser(description="Build a SQLite + FTS5 chat search database.")
    parser.add_argument(
        "--input",
        action="append",
        dest="inputs",
        help="Input JSONL file. Can be provided multiple times. Defaults to cleaned + incremental.",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help=f"Output DB path. Default: {DEFAULT_OUTPUT}")
    args = parser.parse_args()

    inputs = [Path(p) for p in args.inputs] if args.inputs else DEFAULT_INPUTS
    build_sqlite_fts(inputs, Path(args.output))


if __name__ == "__main__":
    main()
