import argparse
import json
from collections import Counter
import re
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUTS = [
    PROJECT_ROOT / "chat_data" / "cleaned_chats.jsonl",
    PROJECT_ROOT / "chat_data" / "chatgpt_incremental_deduped.jsonl",
]
DEFAULT_OUTPUT = PROJECT_ROOT / "chat_data" / "chat_search.db"
VALID_KINDS = {"chat", "summary", "meta", "noise"}
SUMMARY_CARD_RE = re.compile(
    r"(^|\n)##\s+\d+\.\s+.+?\n.*?Tag[：:].*?\n.*?(时间戳|timestamp)[：:].*?\n.*?(发生了什么|what happened)[：:]",
    re.IGNORECASE | re.DOTALL,
)
SUMMARY_MARKERS = (
    "[summary of previous conversation]",
    "summary of previous conversation",
    "## summary",
    "# summary",
    "conversation summary",
    "长期记忆摘要",
    "对话摘要",
    "窗口摘要",
    "总结卡片",
)
SUMMARY_TITLE_MARKERS = (
    "summary",
    "daily summary",
    "rolling summary",
    "conversation summary",
)
NOISE_MARKERS = (
    "http error",
    "http 400",
    "http 401",
    "http 403",
    "http 404",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "traceback (most recent call last)",
    "error:",
    "exception:",
    "failed to",
    "connection error",
    "read timed out",
    "rate limit",
    "bad gateway",
    "service unavailable",
)
META_MARKERS = (
    "chat-proxy",
    "kmlog-search",
    "sqlite",
    "fts5",
    "build_sqlite_fts",
    "cleaned_chats.jsonl",
    "manual_daily_summary",
    "daily_summary",
    "rolling summary",
    "import_scripts",
    "schema",
    "database",
)
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


def classify_kind(row):
    raw_kind = str(row.get("kind") or "").strip().lower()
    if raw_kind in VALID_KINDS:
        return raw_kind

    content = str(row.get("content") or "")
    title = str(row.get("conversation_title") or "")
    text = f"{title}\n{content}".lower()

    if _has_any(text, NOISE_MARKERS) or _looks_like_error_noise(content):
        return "noise"
    if (
        SUMMARY_CARD_RE.search(content)
        or _has_any(text, SUMMARY_MARKERS)
        or _looks_like_summary_title(title)
    ):
        return "summary"
    if _has_any(text, META_MARKERS):
        return "meta"
    return "chat"


def _looks_like_summary_title(title):
    normalized = title.strip().lower()
    return normalized in SUMMARY_TITLE_MARKERS or normalized.endswith(" summary")

def _has_any(text, markers):
    return any(marker in text for marker in markers)


def _looks_like_error_noise(content):
    stripped = content.strip()
    if len(stripped) > 1200:
        return False
    lowered = stripped.lower()
    return (
        lowered.startswith("http") and "error" in lowered
    ) or lowered.startswith(("error ", "error:", "exception ", "exception:"))

def build_sqlite_fts(input_files=DEFAULT_INPUTS, output_file=DEFAULT_OUTPUT):
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    seen_message_ids = set()
    rows = []
    kind_counts = Counter()
    duplicate_rows = 0

    for _path, _line_number, row in iter_jsonl_messages([Path(p) for p in input_files]):
        message_id = row["message_id"]
        if message_id in seen_message_ids:
            duplicate_rows += 1
            continue

        seen_message_ids.add(message_id)
        kind = classify_kind(row)
        kind_counts[kind] += 1
        rows.append(tuple(row[field] for field in REQUIRED_FIELDS) + (kind,))

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
    print("  Kind counts:")
    for kind in sorted(kind_counts):
        print(f"    {kind}: {kind_counts[kind]}")


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



