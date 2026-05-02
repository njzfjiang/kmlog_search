import os
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from typing import Optional, List, Any

SEARCH_BACKEND = os.getenv("KMLOG_SEARCH_BACKEND", "sqlite").strip().lower()

if SEARCH_BACKEND == "supabase":
    try:
        from search_supabase import ensure_search_indexes, search_messages, search_by_date
    except ModuleNotFoundError as exc:
        if exc.name != "search_supabase":
            raise
        from servers.search_supabase import ensure_search_indexes, search_messages, search_by_date
else:
    try:
        from search_sqlite import ensure_search_indexes, search_messages, search_by_date
    except ModuleNotFoundError as exc:
        if exc.name != "search_sqlite":
            raise
        from servers.search_sqlite import ensure_search_indexes, search_messages, search_by_date

APP_TOKEN = os.getenv("SEARCH_API_TOKEN", "")

app = FastAPI(title="KMLog Search API", version="0.1")


def auth(x_api_key: Optional[str]) -> None:
    if not APP_TOKEN:
        # allow local dev without token
        return
    if not x_api_key or x_api_key != APP_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


class SearchReq(BaseModel):
    query: str
    limit: int = 10
    mode: str = "auto"


class SearchByDateReq(BaseModel):
    start_date: str  # "YYYY-MM-DD" or timestamp string compatible with DB
    end_date: str
    role: Optional[str] = None
    limit: int = 20


@app.get("/healthz")
def healthz():
    return {"ok": True, "backend": SEARCH_BACKEND}


@app.post("/ensure_indexes")
def api_ensure_indexes(x_api_key: Optional[str] = Header(default=None)):
    auth(x_api_key)
    ensure_search_indexes()
    return {"ok": True}


@app.post("/search")
def api_search(req: SearchReq, x_api_key: Optional[str] = Header(default=None)):
    auth(x_api_key)
    rows, hit_count = search_messages(req.query, limit=req.limit, mode=req.mode)
    # rows: (id, timestamp, role, content_preview, conversation_title, relevance, match_type)
    return {
        "query": req.query,
        "mode": req.mode,
        "results": [
            {
                "id": r[0],
                "timestamp": str(r[1]),
                "role": r[2],
                "content_preview": r[3],
                "conversation_title": r[4],
                "relevance": float(r[5]),
                "match_type": r[6],
                "token_hits": int(hit_count.get(r[0], 0)),
            }
            for r in rows
        ],
    }


@app.post("/search_by_date")
def api_search_by_date(req: SearchByDateReq, x_api_key: Optional[str] = Header(default=None)):
    auth(x_api_key)
    rows = search_by_date(req.start_date, req.end_date, role=req.role, limit=req.limit)
    # rows: (id, timestamp, role, substr(content, 1, 100), conversation_title)
    return {
        "start_date": req.start_date,
        "end_date": req.end_date,
        "role": req.role,
        "results": [
            {
                "id": r[0],
                "timestamp": str(r[1]),
                "role": r[2],
                "content_preview": r[3],
                "conversation_title": r[4],
            }
            for r in rows
        ],
    }
