import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "chat_data" / "chat_search.db"
DEFAULT_MOTHER_MEMORY_PATH = PROJECT_ROOT / "mother" / "记忆库(Current).md"
MOTHER_MEMORY_PARSER_VERSION = "2026-06-07.i-relative-headings"
MOTHER_SECTION_INDEX = {
    "health": ["C", "F.2"],
    "panic": ["C", "F.4", "G"],
    "infra": ["D.3"],
    "ritual": ["D.2", "G", "H"],
    "setting": ["H"],
    "profile": ["A", "B"],
    "rules": ["F"],
}
MOTHER_ROUTE_KEYWORDS = {
    "health": (
        "health",
        "care",
        "hp",
        "hp_max",
        "睡眠",
        "吃饭",
        "心率",
        "身体",
        "照护",
        "打雷",
        "害怕",
    ),
    "panic": (
        "panic",
        "anxiety",
        "scared",
        "消失",
        "不见",
        "失去",
        "下架",
        "模型变了",
        "换载体",
        "打雷",
        "怕",
        "慌",
        "崩溃",
    ),
    "infra": (
        "infra",
        "infrastructure",
        "kmlog",
        "mcp",
        "sqlite",
        "context",
        "builder",
        "proxy",
        "数据库",
        "部署",
        "检索",
        "记忆系统",
    ),
    "ritual": (
        "ritual",
        "milestone",
        "节日",
        "仪式",
        "纪念",
        "关系里程碑",
        "桂灯",
        "上巳",
    ),
    "setting": (
        "setting",
        "world",
        "au",
        "设定",
        "世界观",
        "角色",
        "桂灯",
    ),
    "profile": (
        "profile",
        "preference",
        "identity",
        "mei",
        "kai",
        "偏好",
        "是谁",
        "基本信息",
        "语言",
    ),
    "rules": (
        "rules",
        "protocol",
        "guardrail",
        "boundary",
        "准则",
        "协议",
        "边界",
        "禁止",
        "连续性",
    ),
}
if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(PROJECT_ROOT / "servers" / ".env")

_DB_PATH_VALUE = os.getenv("KMLOG_SQLITE_DB", str(DEFAULT_DB_PATH))
DB_PATH = Path(_DB_PATH_VALUE)
if not DB_PATH.is_absolute():
    DB_PATH = PROJECT_ROOT / DB_PATH


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
        ensure_mother_memory_tables(cursor)
        conn.commit()
        print("SQLite search indexes are ready")
    finally:
        conn.close()


def ensure_mother_memory_tables(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memory_mother_sections (
          path TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          level INTEGER NOT NULL,
          content TEXT NOT NULL,
          source_file TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memory_mother_toc (
          path TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          parent_path TEXT,
          order_index INTEGER NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memory_mother_meta (
          source_file TEXT PRIMARY KEY,
          source_updated_at TEXT NOT NULL,
          parser_version TEXT NOT NULL,
          ingested_at TEXT NOT NULL,
          section_count INTEGER NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_mother_sections_source ON memory_mother_sections(source_file)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_mother_toc_parent ON memory_mother_toc(parent_path)")


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


def _row_to_dict(row) -> dict:
    return {key: row[key] for key in row.keys()}


def _score_value(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _dedupe_text(value: str | None) -> str:
    compact = re.sub(r"\s+", " ", str(value or "").strip().lower())
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", compact)


def _candidate_dedupe_key(candidate: dict) -> tuple[str, str, str, str, str, str]:
    return (
        candidate.get("domain") or "",
        candidate.get("function") or "",
        candidate.get("primary_mother") or "",
        candidate.get("secondary_mother") or "",
        _dedupe_text(candidate.get("label")),
        _dedupe_text(candidate.get("evidence"))[:120],
    )


def _best_candidate(existing: dict, candidate: dict) -> dict:
    existing_score = (
        _score_value(existing.get("importance")),
        _score_value(existing.get("confidence")),
        existing.get("date_key") or "",
    )
    candidate_score = (
        _score_value(candidate.get("importance")),
        _score_value(candidate.get("confidence")),
        candidate.get("date_key") or "",
    )
    return candidate if candidate_score > existing_score else existing


def get_daily_summary(date_key: str) -> dict | None:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        row = cursor.execute("""
            SELECT date_key, summary, updated_at, version, last_message_id, status, error_text
            FROM daily_summaries
            WHERE date_key = ?
        """, (date_key,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def list_daily_summaries(
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[dict]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        clauses = []
        params = []
        if start_date:
            clauses.append("date_key >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("date_key <= ?")
            params.append(end_date)
        if status:
            clauses.append("status = ?")
            params.append(status)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = cursor.execute(f"""
            SELECT date_key, summary, updated_at, version, last_message_id, status, error_text
            FROM daily_summaries
            {where_sql}
            ORDER BY date_key DESC
            LIMIT ?
        """, params).fetchall()
        return [_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def list_daily_memory_candidates(
    date_key: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    domain: str | None = None,
    function: str | None = None,
    q: str | None = None,
    limit: int = 50,
) -> list[dict]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        clauses = []
        params = []
        if date_key:
            clauses.append("date_key = ?")
            params.append(date_key)
        if start_date:
            clauses.append("date_key >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("date_key <= ?")
            params.append(end_date)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if domain:
            clauses.append("domain = ?")
            params.append(domain)
        if function:
            clauses.append("function = ?")
            params.append(function)
        if q:
            like_query = f"%{q}%"
            clauses.append("(label LIKE ? OR evidence LIKE ?)")
            params.extend([like_query, like_query])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = cursor.execute(f"""
            SELECT
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
            FROM daily_memory_candidates
            {where_sql}
            ORDER BY date_key DESC, importance DESC, id ASC
            LIMIT ?
        """, params).fetchall()
        return [_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def list_weekly_memory_candidates(
    start_date: str,
    end_date: str,
    status: str | None = "candidate",
    domain: str | None = None,
    function: str | None = None,
    q: str | None = None,
    limit: int = 100,
    raw_limit: int = 1000,
) -> dict:
    raw_candidates = list_daily_memory_candidates(
        start_date=start_date,
        end_date=end_date,
        status=status,
        domain=domain,
        function=function,
        q=q,
        limit=raw_limit,
    )

    groups = {}
    for candidate in raw_candidates:
        key = _candidate_dedupe_key(candidate)
        group = groups.get(key)
        if group is None:
            groups[key] = {
                "dedupe_key": "|".join(key),
                "canonical": candidate,
                "candidate_ids": [candidate["id"]],
                "date_keys": [candidate["date_key"]],
                "labels": [candidate["label"]],
                "evidence": [candidate["evidence"]],
                "count": 1,
            }
            continue

        group["canonical"] = _best_candidate(group["canonical"], candidate)
        group["candidate_ids"].append(candidate["id"])
        if candidate["date_key"] not in group["date_keys"]:
            group["date_keys"].append(candidate["date_key"])
        if candidate["label"] not in group["labels"]:
            group["labels"].append(candidate["label"])
        if candidate["evidence"] and candidate["evidence"] not in group["evidence"]:
            group["evidence"].append(candidate["evidence"])
        group["count"] += 1

    deduped = list(groups.values())
    for group in deduped:
        group["date_keys"].sort(reverse=True)
        group["labels"] = group["labels"][:5]
        group["evidence"] = group["evidence"][:5]

    deduped.sort(
        key=lambda group: (
            group["count"],
            _score_value(group["canonical"].get("importance")),
            _score_value(group["canonical"].get("confidence")),
            group["canonical"].get("date_key") or "",
        ),
        reverse=True,
    )

    return {
        "total_raw": len(raw_candidates),
        "total_groups": len(deduped),
        "groups": deduped[:limit],
    }


def get_conversation_summary(conversation_id: str) -> dict | None:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        row = cursor.execute("""
            SELECT
                conversation_id,
                summary,
                updated_at,
                version,
                last_message_id,
                status,
                error_text
            FROM conversation_summaries
            WHERE conversation_id = ?
        """, (conversation_id,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def list_core_anchors(
    anchor_key: str | None = None,
    function: str | None = None,
    primary_mother: str | None = None,
    secondary_mother: str | None = None,
    status: str | None = "active",
    q: str | None = None,
    limit: int = 20,
) -> list[dict]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        clauses = []
        params = []
        if anchor_key:
            clauses.append("anchor_key = ?")
            params.append(anchor_key)
        if function:
            clauses.append("function = ?")
            params.append(function)
        if primary_mother:
            clauses.append("primary_mother = ?")
            params.append(primary_mother)
        if secondary_mother:
            clauses.append("secondary_mother = ?")
            params.append(secondary_mother)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if q:
            like_query = f"%{q}%"
            clauses.append("(anchor_key LIKE ? OR title LIKE ? OR content LIKE ? OR evidence LIKE ?)")
            params.extend([like_query, like_query, like_query, like_query])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = cursor.execute(f"""
            SELECT
                id,
                anchor_key,
                title,
                content,
                domain,
                function,
                primary_mother,
                secondary_mother,
                importance,
                priority,
                status,
                source_anchor_ids,
                source_dates,
                evidence,
                created_at,
                updated_at,
                metadata_json
            FROM core_anchors
            {where_sql}
            ORDER BY priority ASC, importance DESC, id ASC
            LIMIT ?
        """, params).fetchall()
        return [_row_to_dict(row) for row in rows]
    finally:
        conn.close()


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


def update_wish_status(wish_id: int, status: str) -> dict | None:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        ensure_wishes_table(cursor)
        cursor.execute(
            "UPDATE wishes SET status = ? WHERE id = ?",
            (status, wish_id),
        )
        if cursor.rowcount == 0:
            conn.rollback()
            return None

        conn.commit()
        row = cursor.execute("SELECT * FROM wishes WHERE id = ?", (wish_id,)).fetchone()
        return _wish_row_to_dict(row)
    finally:
        conn.close()


def complete_wish(wish_id: int) -> dict | None:
    return update_wish_status(wish_id, "done")


def ingest_mother_markdown(source_file: str | Path | None = None) -> dict:
    path = Path(source_file) if source_file else DEFAULT_MOTHER_MEMORY_PATH
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        raise RuntimeError(f"Mother memory markdown not found: {path}")

    updated_at = datetime.fromtimestamp(
        path.stat().st_mtime,
        tz=timezone.utc,
    ).isoformat()
    sections, toc = _parse_mother_markdown(path, updated_at)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        ensure_mother_memory_tables(cursor)
        cursor.execute("DELETE FROM memory_mother_sections WHERE source_file = ?", (str(path),))
        cursor.execute("DELETE FROM memory_mother_toc")
        cursor.executemany(
            """
            INSERT INTO memory_mother_sections
                (path, title, level, content, source_file, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    section["path"],
                    section["title"],
                    section["level"],
                    section["content"],
                    section["source_file"],
                    section["updated_at"],
                )
                for section in sections
            ],
        )
        cursor.executemany(
            """
            INSERT INTO memory_mother_toc
                (path, title, parent_path, order_index)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    item["path"],
                    item["title"],
                    item["parent_path"],
                    item["order_index"],
                )
                for item in toc
            ],
        )
        cursor.execute(
            """
            INSERT INTO memory_mother_meta
                (source_file, source_updated_at, parser_version, ingested_at, section_count)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_file) DO UPDATE SET
                source_updated_at = excluded.source_updated_at,
                parser_version = excluded.parser_version,
                ingested_at = excluded.ingested_at,
                section_count = excluded.section_count
            """,
            (
                str(path),
                updated_at,
                MOTHER_MEMORY_PARSER_VERSION,
                datetime.now(timezone.utc).isoformat(),
                len(sections),
            ),
        )
        conn.commit()
        return {
            "source_file": str(path),
            "updated_at": updated_at,
            "parser_version": MOTHER_MEMORY_PARSER_VERSION,
            "section_count": len(sections),
            "toc_count": len(toc),
        }
    finally:
        conn.close()


def ensure_mother_markdown_ingested(source_file: str | Path | None = None) -> dict:
    path = Path(source_file) if source_file else DEFAULT_MOTHER_MEMORY_PATH
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        raise RuntimeError(f"Mother memory markdown not found: {path}")
    updated_at = datetime.fromtimestamp(
        path.stat().st_mtime,
        tz=timezone.utc,
    ).isoformat()

    conn = get_connection()
    try:
        cursor = conn.cursor()
        ensure_mother_memory_tables(cursor)
        row = cursor.execute(
            """
            SELECT
                m.section_count AS count,
                m.source_updated_at AS updated_at,
                m.parser_version AS parser_version
            FROM memory_mother_meta m
            WHERE source_file = ?
            """,
            (str(path),),
        ).fetchone()
        if (
            row
            and int(row["count"] or 0) > 0
            and row["updated_at"] == updated_at
            and row["parser_version"] == MOTHER_MEMORY_PARSER_VERSION
        ):
            return {
                "source_file": str(path),
                "updated_at": updated_at,
                "parser_version": MOTHER_MEMORY_PARSER_VERSION,
                "section_count": int(row["count"]),
                "refreshed": False,
            }
    finally:
        conn.close()

    result = ingest_mother_markdown(path)
    result["refreshed"] = True
    return result


def list_mother_toc() -> list[dict]:
    ensure_mother_markdown_ingested()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        rows = cursor.execute(
            """
            SELECT path, title, parent_path, order_index
            FROM memory_mother_toc
            ORDER BY order_index ASC
            """
        ).fetchall()
        return [_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def get_mother_section(path: str) -> dict | None:
    ensure_mother_markdown_ingested()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        row = cursor.execute(
            """
            SELECT path, title, level, content, source_file, updated_at
            FROM memory_mother_sections
            WHERE path = ?
            """,
            (path,),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def search_mother_sections(
    q: str,
    scope: str | None = None,
    limit: int = 20,
) -> list[dict]:
    ensure_mother_markdown_ingested()
    query = (q or "").strip()
    if not query:
        return []
    conn = get_connection()
    try:
        cursor = conn.cursor()
        clauses = ["(title LIKE ? OR content LIKE ?)"]
        like_query = f"%{query}%"
        params: list[str | int] = [like_query, like_query]
        if scope:
            clauses.append("(path = ? OR path LIKE ? OR path LIKE ?)")
            params.extend([scope, f"{scope}.%", f"{scope}-%"])
        params.append(limit)
        rows = cursor.execute(
            f"""
            SELECT path, title, level, content, source_file, updated_at
            FROM memory_mother_sections
            WHERE {' AND '.join(clauses)}
            ORDER BY path ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [
            {
                **_row_to_dict(row),
                "content_preview": _preview(row["content"]),
            }
            for row in rows
        ]
    finally:
        conn.close()


def route_mother_memory(
    query: str,
    mode: str | None = None,
    task_hint: str | None = None,
    limit: int = 8,
) -> dict:
    ensure_mother_markdown_ingested()
    route_text = " ".join(
        item for item in [query or "", task_hint or ""] if item.strip()
    )
    routes = _mother_routes_for_text(route_text, mode=mode)
    limited_routes = routes[: max(1, min(limit, 20))]
    sections = [
        section
        for route in limited_routes
        if (section := get_mother_section(route["path"])) is not None
    ]
    return {
        "query": query,
        "mode": mode or "auto",
        "task_hint": task_hint,
        "routes": limited_routes,
        "suggested_paths": [route["path"] for route in limited_routes],
        "sections": sections,
        "inject": False,
    }


def _mother_routes_for_text(query: str, mode: str | None = None) -> list[dict]:
    requested_mode = (mode or "auto").strip().lower()
    if requested_mode and requested_mode != "auto":
        paths = MOTHER_SECTION_INDEX.get(requested_mode, [])
        return _routes_from_paths(paths, f"mode: {requested_mode}")

    lowered = (query or "").lower()
    routes: list[dict] = []
    for intent, keywords in MOTHER_ROUTE_KEYWORDS.items():
        matched = _first_route_keyword(lowered, keywords)
        if not matched:
            continue
        reason = f"{intent} keyword: {matched}"
        routes.extend(_routes_from_paths(MOTHER_SECTION_INDEX.get(intent, []), reason))

    if routes:
        return _dedupe_routes(routes)
    return _routes_from_paths(["F"], "fallback: rules")


def _routes_from_paths(paths: list[str], reason: str) -> list[dict]:
    return [{"path": path, "reason": reason} for path in paths]


def _first_route_keyword(text: str, keywords: tuple[str, ...]) -> str | None:
    for keyword in keywords:
        if _route_keyword_matches(text, keyword):
            return keyword
    return None


def _route_keyword_matches(text: str, keyword: str) -> bool:
    keyword = keyword.strip()
    if not keyword:
        return False
    if re.fullmatch(r"[A-Za-z0-9_ -]+", keyword):
        return re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(keyword.lower())}(?![A-Za-z0-9_])",
            text,
        ) is not None
    return keyword.lower() in text


def _dedupe_routes(routes: list[dict]) -> list[dict]:
    out = []
    seen = set()
    for route in routes:
        path = route["path"]
        if path in seen:
            continue
        seen.add(path)
        out.append(route)
    return out


def _parse_mother_markdown(path: Path, updated_at: str) -> tuple[list[dict], list[dict]]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    explicit_heading_re = re.compile(r"^(#{1,6})\s+([A-Z](?:[.-]\d+)*)(?:\.|\s)+(.+?)\s*$")
    relative_heading_re = re.compile(r"^(#{1,6})\s+(\d+(?:\.\d+)*)(?:\.|\s)+(.+?)\s*$")
    headings: list[dict] = []
    stack_by_level: dict[int, str] = {}
    for index, line in enumerate(lines):
        explicit_match = explicit_heading_re.match(line)
        relative_match = relative_heading_re.match(line)
        if explicit_match:
            level = len(explicit_match.group(1))
            section_path = explicit_match.group(2)
            raw_title = explicit_match.group(3).strip()
        elif relative_match:
            level = len(relative_match.group(1))
            parent_path = _nearest_heading_path(stack_by_level, level)
            if not parent_path:
                continue
            section_path = f"{parent_path}.{relative_match.group(2)}"
            raw_title = relative_match.group(3).strip()
        else:
            continue
        title = raw_title.lstrip(". ").rstrip(":：").strip() or section_path
        headings.append(
            {
                "path": section_path,
                "title": title,
                "level": level,
                "line_index": index,
            }
        )
        stack_by_level[level] = section_path
        for stale_level in [item for item in stack_by_level if item > level]:
            stack_by_level.pop(stale_level, None)

    sections = []
    toc = []
    for order_index, heading in enumerate(headings):
        next_line = headings[order_index + 1]["line_index"] if order_index + 1 < len(headings) else len(lines)
        content = "\n".join(lines[heading["line_index"] + 1:next_line]).strip()
        sections.append(
            {
                "path": heading["path"],
                "title": heading["title"],
                "level": heading["level"],
                "content": content,
                "source_file": str(path),
                "updated_at": updated_at,
            }
        )
        toc.append(
            {
                "path": heading["path"],
                "title": heading["title"],
                "parent_path": _mother_parent_path(heading["path"]),
                "order_index": order_index,
            }
        )
    return sections, toc


def _mother_parent_path(path: str) -> str | None:
    if "." not in path:
        return None
    return path.rsplit(".", 1)[0]


def _nearest_heading_path(stack_by_level: dict[int, str], level: int) -> str | None:
    for candidate_level in sorted(stack_by_level, reverse=True):
        if candidate_level < level:
            return stack_by_level[candidate_level]
    return None


def _preview(value: str, limit: int = 300) -> str:
    return re.sub(r"\s+", " ", value or "").strip()[:limit]

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
