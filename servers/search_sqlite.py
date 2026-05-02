import os
import re
import sqlite3
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "chat_data" / "chat_search.db"
DB_PATH = Path(os.getenv("KMLOG_SQLITE_DB", str(DEFAULT_DB_PATH)))


def get_connection():
    if not DB_PATH.exists():
        raise RuntimeError(f"SQLite DB not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_search_indexes():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("CREATE INDEX IF NOT EXISTS messages_timestamp_idx ON messages(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS messages_role_idx ON messages(role)")
        cursor.execute("CREATE INDEX IF NOT EXISTS messages_conversation_idx ON messages(conversation_id)")
        conn.commit()
        print("SQLite search indexes are ready")
    finally:
        conn.close()


def split_tokens_for_fallback(q: str, max_tokens: int = 8):
    q = (q or "").strip()
    if not q:
        return []

    parts = [p for p in re.split(r"\s+", q) if p]
    compact = re.sub(r"\s+", "", q)
    if compact and compact not in parts:
        parts.append(compact)

    parts = [p for p in parts if len(p) >= 2]
    seen = set()
    out = []
    for part in parts:
        if part not in seen:
            seen.add(part)
            out.append(part)
    return out[:max_tokens]


def escape_fts5_query(query: str) -> str:
    """
    Keep FTS5 input boring and robust. Quoted phrase search works for CJK
    substrings often enough with unicode61, and LIKE covers the rest.
    """
    query = (query or "").strip().replace('"', '""')
    return f'"{query}"' if query else '""'


def _search_messages_single(query: str, limit: int = 10):
    query_raw = (query or "").strip()
    if not query_raw:
        return []

    query_compact = re.sub(r"\s+", "", query_raw)
    like_query = f"%{query_raw}%"
    like_query_compact = f"%{query_compact}%"
    fts_query = escape_fts5_query(query_raw)

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            WITH fts_hits AS (
                SELECT rowid, bm25(messages_fts) AS bm25_score
                FROM messages_fts
                WHERE messages_fts MATCH ?
            )
            SELECT
                messages.id,
                messages.timestamp,
                messages.role,
                substr(messages.content, 1, 160) AS content_preview,
                messages.conversation_title,
                (
                    CASE WHEN fts_hits.rowid IS NOT NULL THEN 10.0 / (1.0 + ABS(fts_hits.bm25_score)) ELSE 0 END
                    + CASE WHEN messages.conversation_title LIKE ? THEN 0.8 ELSE 0 END
                    + CASE WHEN messages.content LIKE ? THEN 0.4 ELSE 0 END
                    + CASE WHEN messages.conversation_title LIKE ? THEN 0.4 ELSE 0 END
                    + CASE WHEN messages.content LIKE ? THEN 0.2 ELSE 0 END
                ) AS relevance,
                CASE
                    WHEN fts_hits.rowid IS NOT NULL THEN 'fts'
                    WHEN messages.conversation_title LIKE ?
                      OR messages.conversation_title LIKE ? THEN 'title'
                    ELSE 'content'
                END AS match_type
            FROM messages
            LEFT JOIN fts_hits ON fts_hits.rowid = messages.rowid
            WHERE fts_hits.rowid IS NOT NULL
               OR messages.content LIKE ?
               OR messages.conversation_title LIKE ?
               OR messages.content LIKE ?
               OR messages.conversation_title LIKE ?
            ORDER BY relevance DESC, timestamp DESC
            LIMIT ?
        """, (
            fts_query,
            like_query,
            like_query,
            like_query_compact,
            like_query_compact,
            like_query,
            like_query_compact,
            like_query,
            like_query,
            like_query_compact,
            like_query_compact,
            limit,
        ))

        return [tuple(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def _search_messages_tokens(query: str, limit: int = 10):
    tokens = split_tokens_for_fallback(query)
    if len(tokens) <= 1:
        return [], {}

    merged = {}
    hit_count = defaultdict(int)
    best_rel = defaultdict(float)
    per_token_limit = max(10, limit)

    for token in tokens:
        rows = _search_messages_single(token, limit=per_token_limit)
        for row in rows:
            message_pk = row[0]
            hit_count[message_pk] += 1
            best_rel[message_pk] = max(best_rel[message_pk], float(row[5] or 0))
            merged[message_pk] = row

    ranked = sorted(
        merged.values(),
        key=lambda row: (hit_count[row[0]], best_rel[row[0]], row[1]),
        reverse=True,
    )
    return ranked[:limit], hit_count


def search_messages(query: str, limit: int = 10, mode: str = "auto"):
    query = (query or "").strip()
    if not query:
        return [], {}

    tokens = split_tokens_for_fallback(query)
    if mode == "auto":
        mode = "tokens" if len(tokens) >= 2 else "phrase"

    if mode == "tokens":
        ranked, hit_count = _search_messages_tokens(query, limit=limit)
        if ranked:
            return ranked, hit_count
        return _search_messages_single(query, limit=limit), {}

    base = _search_messages_single(query, limit=limit)
    if base:
        return base, {}

    return _search_messages_tokens(query, limit=limit)


def search_by_date(start_date, end_date, role=None, limit=20):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        params = [start_date, end_date]
        role_filter = ""
        if role:
            role_filter = "AND role = ?"
            params.append(role)
        params.append(limit)

        cursor.execute(f"""
            SELECT id, timestamp, role, substr(content, 1, 100), conversation_title
            FROM messages
            WHERE timestamp BETWEEN ? AND ?
            {role_filter}
            ORDER BY timestamp DESC
            LIMIT ?
        """, params)

        return [tuple(row) for row in cursor.fetchall()]
    finally:
        conn.close()


if __name__ == "__main__":
    ensure_search_indexes()
    rows, hit_count = search_messages("ChatGPT", limit=5)
    for row in rows:
        print(row, "hits:", hit_count.get(row[0], 0))
