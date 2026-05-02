import psycopg2
import os
import re
from dotenv import load_dotenv
from collections import defaultdict

load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL')
TEXT_SEARCH_CONFIG = 'english'

def pick_fts_config(query: str) -> str:
    # If contains CJK, use simple (better than english for mixed text)
    if re.search(r"[\u4e00-\u9fff]", query or ""):
        return "simple"
    return "english"

def get_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL未设置")

    return psycopg2.connect(DATABASE_URL, sslmode='require')


def ensure_search_indexes():
    """
    创建中英文混合搜索需要的索引。
    第一次运行会花一点时间；之后会自动跳过已存在的索引。
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS messages_content_trgm_idx
            ON messages USING GIN (content gin_trgm_ops)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS messages_title_trgm_idx
            ON messages USING GIN (conversation_title gin_trgm_ops)
        ''')
        conn.commit()
        conn.close()
        print("✅ 混合搜索索引已准备好")
    except Exception as e:
        print(f"❌ 创建搜索索引失败: {e}")



def split_tokens_for_fallback(q: str, max_tokens: int = 8):
    """Split query into tokens for fallback search."""
    q = (q or "").strip()
    if not q:
        return []
    # keep original whitespace tokens
    parts = [p for p in re.split(r"\s+", q) if p]
    # add compact version (remove all spaces)
    compact = re.sub(r"\s+", "", q)
    if compact and compact not in parts:
        parts.append(compact)

    # drop very short tokens (avoid noise)
    parts = [p for p in parts if len(p) >= 2]
    # de-dup keep order
    seen = set()
    out = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out[:max_tokens]


def _search_messages_single(query: str, limit: int = 10):
    """Your current single-query search (raw+compact) — unchanged SQL logic."""
    query_raw = query
    query_compact = re.sub(r"\s+", "", query_raw)

    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            WITH query_input AS (
                SELECT
                    plainto_tsquery(%s, %s) AS ts_query,
                    %s AS raw_query,
                    '%%' || %s || '%%' AS like_query,
                    '%%' || %s || '%%' AS like_query_compact
            )
            SELECT 
                messages.id,
                messages.timestamp,
                messages.role,
                substr(messages.content, 1, 160) AS content_preview,
                messages.conversation_title,
                (
                    ts_rank(messages.content_ts, query_input.ts_query)
                    + CASE WHEN messages.conversation_title ILIKE query_input.like_query THEN 0.8 ELSE 0 END
                    + CASE WHEN messages.content ILIKE query_input.like_query THEN 0.4 ELSE 0 END
                    + CASE WHEN messages.conversation_title ILIKE query_input.like_query_compact THEN 0.4 ELSE 0 END
                    + CASE WHEN messages.content ILIKE query_input.like_query_compact THEN 0.2 ELSE 0 END
                    + 0.35 * similarity(messages.conversation_title, query_input.raw_query)
                    + 0.25 * similarity(messages.content, query_input.raw_query)
                ) AS relevance,
                CASE
                    WHEN messages.content_ts @@ query_input.ts_query THEN 'fts'
                    WHEN messages.conversation_title ILIKE query_input.like_query
                      OR messages.conversation_title ILIKE query_input.like_query_compact THEN 'title'
                    ELSE 'content'
                END AS match_type
            FROM messages, query_input
            WHERE messages.content_ts @@ query_input.ts_query
               OR messages.content ILIKE query_input.like_query
               OR messages.conversation_title ILIKE query_input.like_query
               OR messages.content ILIKE query_input.like_query_compact
               OR messages.conversation_title ILIKE query_input.like_query_compact
            ORDER BY relevance DESC, timestamp DESC
            LIMIT %s
        ''', (
            pick_fts_config(query_raw),
            query_raw,
            query_raw,
            query_raw,
            query_compact,
            limit
        ))

        results = cursor.fetchall()
        conn.close()
        return results

    except Exception as e:
        print(f"❌ 搜索失败: {e}")
        return []

def _search_messages_tokens(query: str, limit: int = 10):
    """
    Token-OR search: split query to tokens, search each token, merge+rank.
    Returns: (ranked_rows, hit_count_map)
    """
    tokens = split_tokens_for_fallback(query)
    if len(tokens) <= 1:
        return [], {}

    merged = {}  # id -> row
    hit_count = defaultdict(int)
    best_rel = defaultdict(float)

    per_token_limit = max(10, limit)

    for t in tokens:
        rows = _search_messages_single(t, limit=per_token_limit)
        for r in rows:
            mid = r[0]
            hit_count[mid] += 1
            try:
                rel = float(r[5])
            except Exception:
                rel = 0.0
            if rel > best_rel[mid]:
                best_rel[mid] = rel
            merged[mid] = r

    if not merged:
        return [], {}

    ranked = sorted(
        merged.values(),
        key=lambda r: (hit_count[r[0]], best_rel[r[0]], r[1]),
        reverse=True,
    )
    return ranked[:limit], hit_count

def search_messages(query: str, limit: int = 10, mode: str = "auto"):
    """
    Hybrid search with robust multi-keyword behavior.

    mode:
      - "auto": if multi-token => tokens, else phrase
      - "phrase": prefer full query; if empty => fallback tokens
      - "tokens": token-OR merge first (best for multi-keyword)
    """
    query = (query or "").strip()
    if not query:
        return [], {}

    tokens = split_tokens_for_fallback(query)
    multi = len(tokens) >= 2

    if mode == "auto":
        mode = "tokens" if multi else "phrase"

    if mode == "tokens":
        ranked, hit_count = _search_messages_tokens(query, limit=limit)
        # if tokens mode somehow yields nothing, fall back to phrase
        if ranked:
            return ranked, hit_count
        base = _search_messages_single(query, limit=limit)
        return base, {}

    # phrase mode (your current behavior) + fallback
    base = _search_messages_single(query, limit=limit)
    if base:
        return base, {}

    ranked, hit_count = _search_messages_tokens(query, limit=limit)
    return ranked, hit_count

def search_by_date(start_date, end_date, role=None, limit=20):
    """
    按日期范围搜索
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        if role:
            cursor.execute('''
                SELECT id, timestamp, role, substr(content, 1, 100), conversation_title
                FROM messages
                WHERE timestamp BETWEEN %s AND %s
                AND role = %s
                ORDER BY timestamp DESC
                LIMIT %s
            ''', (start_date, end_date, role, limit))
        else:
            cursor.execute('''
                SELECT id, timestamp, role, substr(content, 1, 100), conversation_title
                FROM messages
                WHERE timestamp BETWEEN %s AND %s
                ORDER BY timestamp DESC
                LIMIT %s
            ''', (start_date, end_date, limit))
        
        results = cursor.fetchall()
        conn.close()
        return results
        
    except Exception as e:
        print(f"❌ 搜索失败: {e}")
        return []

if __name__ == "__main__":
    ensure_search_indexes()

    # 示例搜索
    print("\n🔍 混合搜索示例：")
    results, _hit_count = search_messages("ChatGPT", limit=5)
    for row in results:
        print(f"  ID: {row[0]}, Time: {row[1]}, Role: {row[2]}")
        print(f"  Content: {row[3]}...")
        print(f"  Conv: {row[4]}, Relevance: {row[5]:.2f}, Match: {row[6]}\n")

    print("\n🔍 混合搜索示例：")
    results, _hit_count = search_messages("桂郎", limit=5)
    for row in results:
        print(f"  ID: {row[0]}, Time: {row[1]}, Role: {row[2]}")
        print(f"  Content: {row[3]}...")
        print(f"  Conv: {row[4]}, Relevance: {row[5]:.2f}, Match: {row[6]}\n")

    print("\n📅 日期范围搜索示例：")
    results = search_by_date("2026-04-29", "2026-04-30", role="user", limit=3)
    for row in results:
        print(f"  {row[1]} [{row[2]}]: {row[3]}...")
