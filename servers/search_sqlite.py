import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
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
        columns = {row[1] for row in cursor.execute("PRAGMA table_info(messages)").fetchall()}
        if "kind" not in columns:
            cursor.execute("ALTER TABLE messages ADD COLUMN kind TEXT DEFAULT 'chat'")
        cursor.execute("UPDATE messages SET kind = 'chat' WHERE kind IS NULL OR kind = ''")
        cursor.execute("CREATE INDEX IF NOT EXISTS messages_timestamp_idx ON messages(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS messages_role_idx ON messages(role)")
        cursor.execute("CREATE INDEX IF NOT EXISTS messages_conversation_idx ON messages(conversation_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS messages_kind_idx ON messages(kind)")
        ensure_wishes_table(cursor)
        conn.commit()
        print("SQLite search indexes are ready")
    finally:
        conn.close()


def ensure_wishes_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wishes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL,
          owner TEXT NOT NULL,
          scope TEXT NOT NULL,
          text TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'open',
          priority INTEGER DEFAULT 3,
          tags TEXT DEFAULT '',
          source TEXT DEFAULT 'manual'
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_wishes_created_at ON wishes(created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_wishes_status ON wishes(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_wishes_scope ON wishes(scope)")


def ensure_wish_indexes():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        ensure_wishes_table(cursor)
        conn.commit()
        print("SQLite wishes table is ready")
    finally:
        conn.close()


def _wish_row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "owner": row["owner"],
        "scope": row["scope"],
        "text": row["text"],
        "status": row["status"],
        "priority": row["priority"],
        "tags": row["tags"] or "",
        "source": row["source"],
    }


def create_wish(
    owner: str,
    scope: str,
    text: str,
    status: str = "open",
    priority: int = 3,
    tags: str = "",
    source: str = "manual",
    created_at: str | None = None,
) -> dict:
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        ensure_wishes_table(cursor)
        cursor.execute("""
            INSERT INTO wishes (created_at, owner, scope, text, status, priority, tags, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (created_at, owner, scope, text, status, priority, tags or "", source))
        wish_id = cursor.lastrowid
        conn.commit()

        row = cursor.execute("SELECT * FROM wishes WHERE id = ?", (wish_id,)).fetchone()
        return _wish_row_to_dict(row)
    finally:
        conn.close()


def list_wishes(
    wish_id: int | None = None,
    owner: str | None = None,
    scope: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = 50,
) -> list[dict]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        ensure_wishes_table(cursor)

        clauses = []
        params = []
        if wish_id is not None:
            clauses.append("id = ?")
            params.append(wish_id)
        if owner:
            clauses.append("owner = ?")
            params.append(owner)
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if q:
            like_query = f"%{q}%"
            clauses.append("(text LIKE ? OR tags LIKE ?)")
            params.extend([like_query, like_query])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = cursor.execute(f"""
            SELECT *
            FROM wishes
            {where_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
        """, params).fetchall()
        return [_wish_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def complete_wish(wish_id: int) -> dict | None:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        ensure_wishes_table(cursor)
        cursor.execute(
            "UPDATE wishes SET status = 'done' WHERE id = ?",
            (wish_id,),
        )
        if cursor.rowcount == 0:
            conn.rollback()
            return None

        conn.commit()
        row = cursor.execute("SELECT * FROM wishes WHERE id = ?", (wish_id,)).fetchone()
        return _wish_row_to_dict(row)
    finally:
        conn.close()

def _count_token_hits(text: str, tokens: list[str]) -> int:
    t = (text or "")
    hits = 0
    for tok in tokens:
        if tok and tok in t:
            hits += 1
    return hits


def _row_token_text(row) -> str:
    return f"{row[4] or ''}\n{row[3] or ''}"


def _rank_rows_by_token_hits(rows, tokens, limit: int):
    if not rows:
        return [], {}

    token_hits = {
        row[0]: _count_token_hits(_row_token_text(row), tokens)
        for row in rows
    }
    ranked = sorted(
        rows,
        key=lambda row: (token_hits[row[0]], float(row[5] or 0), row[1]),
        reverse=True,
    )
    return ranked[:limit], token_hits


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


def _search_messages_single(
    query: str,
    limit: int = 10,
    kinds: list[str] | None = None,
    after: str | None = None,
    before: str | None = None,
):
    query_raw = (query or "").strip()
    if not query_raw:
        return []

    kinds = [kind for kind in (kinds or ["chat"]) if kind]
    if not kinds:
        kinds = ["chat"]
    kinds_placeholders = ",".join(["?"] * len(kinds))

    query_compact = re.sub(r"\s+", "", query_raw)
    like_query = f"%{query_raw}%"
    like_query_compact = f"%{query_compact}%"
    fts_query = escape_fts5_query(query_raw)

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(f"""
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
    (
      -COALESCE(fts_hits.bm25_score, 0.0)
      + CASE WHEN messages.conversation_title LIKE ? THEN 0.8 ELSE 0 END
      + CASE WHEN messages.content LIKE ? THEN 0.4 ELSE 0 END
      + CASE WHEN messages.conversation_title LIKE ? THEN 0.4 ELSE 0 END
      + CASE WHEN messages.content LIKE ? THEN 0.2 ELSE 0 END
    )
    * CASE messages.kind
        WHEN 'chat' THEN 1.0
        WHEN 'meta' THEN 0.8
        WHEN 'summary' THEN 0.4
        ELSE 1.0
      END
  ) AS relevance,

  CASE
    WHEN fts_hits.rowid IS NOT NULL THEN 'fts'
    WHEN messages.conversation_title LIKE ? OR messages.conversation_title LIKE ? THEN 'title'
    ELSE 'content'
  END AS match_type

FROM messages
LEFT JOIN fts_hits ON fts_hits.rowid = messages.id
WHERE
  messages.kind IN ({kinds_placeholders})
  AND (? IS NULL OR messages.timestamp >= ?)
  AND (? IS NULL OR messages.timestamp <= ?)
  AND (
       fts_hits.rowid IS NOT NULL
    OR messages.content LIKE ?
    OR messages.conversation_title LIKE ?
    OR messages.content LIKE ?
    OR messages.conversation_title LIKE ?
  )
ORDER BY relevance DESC, messages.timestamp DESC
LIMIT ?;""", (
            fts_query,
            like_query,
            like_query,
            like_query_compact,
            like_query_compact,
            like_query,
            like_query_compact,
            *kinds,
            after,
            after,
            before,
            before,
            like_query,
            like_query,
            like_query_compact,
            like_query_compact,
            limit,
        ))

        return [tuple(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def _search_messages_tokens(
    query: str,
    limit: int = 10,
    kinds: list[str] | None = None,
    after: str | None = None,
    before: str | None = None,
):
    tokens = split_tokens_for_fallback(query)
    if len(tokens) <= 1:
        return [], {}

    merged = {}
    hit_count = defaultdict(int)
    best_rel = defaultdict(float)
    per_token_limit = max(10, limit)

    for token in tokens:
        rows = _search_messages_single(
            token,
            limit=per_token_limit,
            kinds=kinds,
            after=after,
            before=before,
        )
        for row in rows:
            message_pk = row[0]
            hit_count[message_pk] += 1
            best_rel[message_pk] = max(best_rel[message_pk], float(row[5] or 0))
            merged[message_pk] = row

    token_hits = {
        row[0]: max(hit_count[row[0]], _count_token_hits(_row_token_text(row), tokens))
        for row in merged.values()
    }
    ranked = sorted(
        merged.values(),
        key=lambda row: (token_hits[row[0]], best_rel[row[0]], row[1]),
        reverse=True,
    )
    return ranked[:limit], token_hits


def search_messages(
    query: str,
    limit: int = 10,
    mode: str = "auto",
    kinds: list[str] | None = None,
    after: str | None = None,
    before: str | None = None,
):
    query = (query or "").strip()
    if not query:
        return [], {}

    tokens = split_tokens_for_fallback(query)
    if mode == "auto":
        mode = "tokens" if len(tokens) >= 2 else "phrase"

    if mode == "tokens":
        ranked, hit_count = _search_messages_tokens(
            query,
            limit=limit,
            kinds=kinds,
            after=after,
            before=before,
        )
        if ranked:
            return ranked, hit_count
        return _rank_rows_by_token_hits(
            _search_messages_single(
                query,
                limit=limit,
                kinds=kinds,
                after=after,
                before=before,
            ),
            tokens,
            limit,
        )

    base = _search_messages_single(
        query,
        limit=limit,
        kinds=kinds,
        after=after,
        before=before,
    )
    if base:
        return _rank_rows_by_token_hits(base, tokens, limit)

    return _search_messages_tokens(
        query,
        limit=limit,
        kinds=kinds,
        after=after,
        before=before,
    )


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
