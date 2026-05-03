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
    ensure_wish_indexes = None
    create_wish = None
    list_wishes = None
else:
    try:
        from search_sqlite import (
            create_wish,
            ensure_search_indexes,
            ensure_wish_indexes,
            list_wishes,
            search_by_date,
            search_messages,
        )
    except ModuleNotFoundError as exc:
        if exc.name != "search_sqlite":
            raise
        from servers.search_sqlite import (
            create_wish,
            ensure_search_indexes,
            ensure_wish_indexes,
            list_wishes,
            search_by_date,
            search_messages,
        )

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
    kinds: Optional[List[str]] = None
    after: Optional[str] = None
    before: Optional[str] = None


class SearchByDateReq(BaseModel):
    start_date: str  # "YYYY-MM-DD" or timestamp string compatible with DB
    end_date: str
    role: Optional[str] = None
    limit: int = 20


class WishReq(BaseModel):
    owner: str
    scope: str
    text: str
    status: str = "open"
    priority: int = 3
    tags: str = ""
    source: str = "manual"
    created_at: Optional[str] = None


WISH_OWNERS = {"Mei", "Kai", "Shared"}
WISH_SCOPES = {"care", "work", "romance", "play", "misc"}
WISH_STATUSES = {"open", "done", "stale", "archived"}
WISH_SOURCES = {"manual", "agent_suggest"}


def _ensure_wishes_supported() -> None:
    if SEARCH_BACKEND != "sqlite" or create_wish is None or list_wishes is None:
        raise HTTPException(status_code=501, detail="Wishes are only implemented for the sqlite backend")


def _validate_choice(name: str, value: Optional[str], allowed: set[str]) -> None:
    if value is not None and value not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {name}: {value}. Allowed values: {', '.join(sorted(allowed))}",
        )


@app.get("/healthz")
def healthz():
    return {"ok": True, "backend": SEARCH_BACKEND}


@app.post("/ensure_indexes")
def api_ensure_indexes(x_api_key: Optional[str] = Header(default=None)):
    auth(x_api_key)
    ensure_search_indexes()
    if ensure_wish_indexes is not None:
        ensure_wish_indexes()
    return {"ok": True}


@app.post("/search")
def api_search(req: SearchReq, x_api_key: Optional[str] = Header(default=None)):
    auth(x_api_key)
    rows, hit_count = search_messages(
        req.query,
        limit=req.limit,
        mode=req.mode,
        kinds=req.kinds,
        after=req.after,
        before=req.before,
    )
    # rows: (id, timestamp, role, content_preview, conversation_title, relevance, match_type)
    return {
        "query": req.query,
        "mode": req.mode,
        "kinds": req.kinds,
        "after": req.after,
        "before": req.before,
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


@app.get("/wish")
def api_get_wish(
    owner: Optional[str] = None,
    scope: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    x_api_key: Optional[str] = Header(default=None),
):
    auth(x_api_key)
    _ensure_wishes_supported()
    _validate_choice("owner", owner, WISH_OWNERS)
    _validate_choice("scope", scope, WISH_SCOPES)
    _validate_choice("status", status, WISH_STATUSES)
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")

    return {
        "owner": owner,
        "scope": scope,
        "status": status,
        "results": list_wishes(owner=owner, scope=scope, status=status, limit=limit),
    }


@app.post("/wish")
def api_post_wish(req: WishReq, x_api_key: Optional[str] = Header(default=None)):
    auth(x_api_key)
    _ensure_wishes_supported()
    _validate_choice("owner", req.owner, WISH_OWNERS)
    _validate_choice("scope", req.scope, WISH_SCOPES)
    _validate_choice("status", req.status, WISH_STATUSES)
    _validate_choice("source", req.source, WISH_SOURCES)

    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if req.priority < 1 or req.priority > 5:
        raise HTTPException(status_code=400, detail="priority must be between 1 and 5")

    wish = create_wish(
        owner=req.owner,
        scope=req.scope,
        text=text,
        status=req.status,
        priority=req.priority,
        tags=req.tags,
        source=req.source,
        created_at=req.created_at,
    )
    return {"ok": True, "wish": wish}
