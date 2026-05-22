import os
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List

SEARCH_BACKEND = os.getenv("KMLOG_SEARCH_BACKEND", "sqlite").strip().lower()

if SEARCH_BACKEND == "supabase":
    try:
        from search_supabase import ensure_search_indexes, search_messages, search_by_date
    except ModuleNotFoundError as exc:
        if exc.name != "search_supabase":
            raise
        from servers.search_supabase import ensure_search_indexes, search_messages, search_by_date
    ensure_wish_indexes = None
    complete_wish = None
    create_wish = None
    get_conversation_summary = None
    get_daily_summary = None
    list_daily_memory_candidates = None
    list_core_anchors = None
    list_daily_summaries = None
    list_wishes = None
    update_wish_status = None
else:
    try:
        from search_sqlite import (
            complete_wish,
            create_wish,
            ensure_search_indexes,
            ensure_wish_indexes,
            get_conversation_summary,
            get_daily_summary,
            list_core_anchors,
            list_daily_memory_candidates,
            list_daily_summaries,
            list_wishes,
            search_by_date,
            search_messages,
            update_wish_status,
        )
    except ModuleNotFoundError as exc:
        if exc.name != "search_sqlite":
            raise
        from servers.search_sqlite import (
            complete_wish,
            create_wish,
            ensure_search_indexes,
            ensure_wish_indexes,
            get_conversation_summary,
            get_daily_summary,
            list_core_anchors,
            list_daily_memory_candidates,
            list_daily_summaries,
            list_wishes,
            search_by_date,
            search_messages,
            update_wish_status,
        )

APP_TOKEN = os.getenv("SEARCH_API_TOKEN", "")
DISABLE_DOCS = os.getenv("KMLOG_DISABLE_DOCS", "").strip().lower() in {"1", "true", "yes"}
SERVER_DIR = Path(__file__).resolve().parent
STATIC_DIR = SERVER_DIR / "static"

app = FastAPI(
    title="KMLog Search API",
    version="0.1",
    docs_url=None if DISABLE_DOCS else "/docs",
    redoc_url=None if DISABLE_DOCS else "/redoc",
    openapi_url=None if DISABLE_DOCS else "/openapi.json",
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


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


class WishStatusReq(BaseModel):
    status: str


WISH_OWNERS = {"Mei", "Kai", "Shared"}
WISH_SCOPES = {"care", "work", "romance", "play", "misc"}
WISH_STATUSES = {"open", "done", "stale", "archived"}
WISH_SOURCES = {"manual", "agent_suggest"}


def _ensure_wishes_supported() -> None:
    if (
        SEARCH_BACKEND != "sqlite"
        or complete_wish is None
        or create_wish is None
        or list_wishes is None
        or update_wish_status is None
    ):
        raise HTTPException(status_code=501, detail="Wishes are only implemented for the sqlite backend")


def _ensure_summaries_supported() -> None:
    if (
        SEARCH_BACKEND != "sqlite"
        or get_conversation_summary is None
        or get_daily_summary is None
        or list_daily_memory_candidates is None
        or list_daily_summaries is None
    ):
        raise HTTPException(status_code=501, detail="Summaries are only implemented for the sqlite backend")


def _ensure_core_anchors_supported() -> None:
    if SEARCH_BACKEND != "sqlite" or list_core_anchors is None:
        raise HTTPException(status_code=501, detail="Core anchors are only implemented for the sqlite backend")


def _validate_choice(name: str, value: Optional[str], allowed: set[str]) -> None:
    if value is not None and value not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {name}: {value}. Allowed values: {', '.join(sorted(allowed))}",
        )


@app.get("/healthz")
def healthz():
    return {"ok": True, "backend": SEARCH_BACKEND}


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt():
    return "User-agent: *\nDisallow: /\n"


@app.get("/wishes", response_class=HTMLResponse)
def wishes_page():
    return (STATIC_DIR / "wishes.html").read_text(encoding="utf-8")


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


@app.get("/daily_summary")
def api_get_daily_summary(
    date_key: str,
    x_api_key: Optional[str] = Header(default=None),
):
    auth(x_api_key)
    _ensure_summaries_supported()
    summary = get_daily_summary(date_key)
    if summary is None:
        raise HTTPException(status_code=404, detail="Daily summary not found")
    return {"date_key": date_key, "summary": summary}


@app.get("/daily_summaries")
def api_list_daily_summaries(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
    x_api_key: Optional[str] = Header(default=None),
):
    auth(x_api_key)
    _ensure_summaries_supported()
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    return {
        "start_date": start_date,
        "end_date": end_date,
        "status": status,
        "results": list_daily_summaries(
            start_date=start_date,
            end_date=end_date,
            status=status,
            limit=limit,
        ),
    }


@app.get("/daily_memory_candidates")
def api_list_daily_memory_candidates(
    date_key: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    domain: Optional[str] = None,
    function: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
    x_api_key: Optional[str] = Header(default=None),
):
    auth(x_api_key)
    _ensure_summaries_supported()
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    return {
        "date_key": date_key,
        "start_date": start_date,
        "end_date": end_date,
        "status": status,
        "domain": domain,
        "function": function,
        "q": q,
        "results": list_daily_memory_candidates(
            date_key=date_key,
            start_date=start_date,
            end_date=end_date,
            status=status,
            domain=domain,
            function=function,
            q=q,
            limit=limit,
        ),
    }


@app.get("/conversation_summary")
def api_get_conversation_summary(
    conversation_id: str,
    x_api_key: Optional[str] = Header(default=None),
):
    auth(x_api_key)
    _ensure_summaries_supported()
    summary = get_conversation_summary(conversation_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Conversation summary not found")
    return {"conversation_id": conversation_id, "summary": summary}


@app.get("/core_anchors")
def api_list_core_anchors(
    anchor_key: Optional[str] = None,
    function: Optional[str] = None,
    primary_mother: Optional[str] = None,
    secondary_mother: Optional[str] = None,
    status: Optional[str] = "active",
    q: Optional[str] = None,
    limit: int = 20,
    x_api_key: Optional[str] = Header(default=None),
):
    auth(x_api_key)
    _ensure_core_anchors_supported()
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    try:
        results = list_core_anchors(
            anchor_key=anchor_key,
            function=function,
            primary_mother=primary_mother,
            secondary_mother=secondary_mother,
            status=status,
            q=q,
            limit=limit,
        )
    except Exception as exc:
        if "core_anchors" in str(exc):
            raise HTTPException(status_code=501, detail="Core anchors table is not available") from exc
        raise
    return {
        "anchor_key": anchor_key,
        "function": function,
        "primary_mother": primary_mother,
        "secondary_mother": secondary_mother,
        "status": status,
        "q": q,
        "results": results,
    }


@app.get("/wish")
def api_get_wish(
    id: Optional[int] = None,
    owner: Optional[str] = None,
    scope: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
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
        "id": id,
        "owner": owner,
        "scope": scope,
        "status": status,
        "q": q,
        "results": list_wishes(wish_id=id, owner=owner, scope=scope, status=status, q=q, limit=limit),
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


@app.post("/wish/{wish_id}/status")
def api_update_wish_status(
    wish_id: int,
    req: WishStatusReq,
    x_api_key: Optional[str] = Header(default=None),
):
    auth(x_api_key)
    _ensure_wishes_supported()
    _validate_choice("status", req.status, WISH_STATUSES)

    wish = update_wish_status(wish_id, req.status)
    if wish is None:
        raise HTTPException(status_code=404, detail="Wish not found")
    return {"ok": True, "wish": wish}


@app.post("/complete_wish/{wish_id}")
def api_complete_wish(wish_id: int, x_api_key: Optional[str] = Header(default=None)):
    auth(x_api_key)
    _ensure_wishes_supported()
    wish = complete_wish(wish_id)
    if wish is None:
        raise HTTPException(status_code=404, detail="Wish not found")
    return {"ok": True, "wish": wish}
