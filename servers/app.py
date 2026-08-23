import os
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from typing import Any, Optional, List

try:
    from .worldbook import (
        WorldBookRevisionConflictError,
        apply_worldbook_update,
        get_worldbook_source_info,
        list_worldbook_entries,
        preview_worldbook_update,
        rebuild_merged_worldbook,
    )
    from .recent_goals import (
        JRevisionConflictError,
        JValidationError,
        apply_j_update,
        get_j_source_info,
        preview_j_update,
    )
except ImportError:
    if __package__:
        raise
    from worldbook import (
        WorldBookRevisionConflictError,
        apply_worldbook_update,
        get_worldbook_source_info,
        list_worldbook_entries,
        preview_worldbook_update,
        rebuild_merged_worldbook,
    )
    from recent_goals import (
        JRevisionConflictError,
        JValidationError,
        apply_j_update,
        get_j_source_info,
        preview_j_update,
    )

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

SERVER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SERVER_DIR.parent
if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(SERVER_DIR / ".env")

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
    list_mother_toc = None
    list_core_anchors = None
    list_daily_summaries = None
    get_mother_section = None
    get_mother_source_info = None
    get_reviewed_memory_by_message = None
    list_reviewed_memory_items = None
    route_mother_memory = None
    list_weekly_memory_candidates = None
    list_wishes = None
    promote_memory_candidate = None
    apply_mother_memory_update = None
    preview_mother_memory_update = None
    update_reviewed_memory_item = None
    update_reviewed_memory_status = None
    search_mother_sections = None
    update_memory_candidate_status = None
    update_wish_status = None
else:
    try:
        from search_sqlite import (
            MotherMemoryRevisionConflictError,
            ReviewedMemoryConflictError,
            ReviewedMemoryNotFoundError,
            apply_mother_memory_update,
            complete_wish,
            create_wish,
            ensure_search_indexes,
            ensure_wish_indexes,
            get_conversation_summary,
            get_daily_summary,
            get_mother_section,
            get_mother_source_info,
            get_reviewed_memory_by_message,
            list_core_anchors,
            list_daily_memory_candidates,
            list_daily_summaries,
            list_mother_toc,
            list_reviewed_memory_items,
            list_weekly_memory_candidates,
            list_wishes,
            promote_memory_candidate,
            preview_mother_memory_update,
            route_mother_memory,
            search_by_date,
            search_mother_sections,
            search_messages,
            update_memory_candidate_status,
            update_reviewed_memory_item,
            update_reviewed_memory_status,
            update_wish_status,
        )
    except ModuleNotFoundError as exc:
        if exc.name != "search_sqlite":
            raise
        from servers.search_sqlite import (
            MotherMemoryRevisionConflictError,
            ReviewedMemoryConflictError,
            ReviewedMemoryNotFoundError,
            apply_mother_memory_update,
            complete_wish,
            create_wish,
            ensure_search_indexes,
            ensure_wish_indexes,
            get_conversation_summary,
            get_daily_summary,
            get_mother_section,
            get_mother_source_info,
            get_reviewed_memory_by_message,
            list_core_anchors,
            list_daily_memory_candidates,
            list_daily_summaries,
            list_mother_toc,
            list_reviewed_memory_items,
            list_weekly_memory_candidates,
            list_wishes,
            promote_memory_candidate,
            preview_mother_memory_update,
            route_mother_memory,
            search_by_date,
            search_mother_sections,
            search_messages,
            update_memory_candidate_status,
            update_reviewed_memory_item,
            update_reviewed_memory_status,
            update_wish_status,
        )

APP_TOKEN = os.getenv("SEARCH_API_TOKEN", "")
MOTHER_WRITE_TOKEN = os.getenv("KMLOG_MOTHER_WRITE_TOKEN", "") or APP_TOKEN
WORLDBOOK_WRITE_TOKEN = os.getenv("KMLOG_WORLDBOOK_WRITE_TOKEN", "") or APP_TOKEN
J_WRITE_TOKEN = os.getenv("KMLOG_J_WRITE_TOKEN", "") or APP_TOKEN
DISABLE_DOCS = os.getenv("KMLOG_DISABLE_DOCS", "").strip().lower() in {"1", "true", "yes"}
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


def mother_write_auth(x_api_key: Optional[str]) -> None:
    if not MOTHER_WRITE_TOKEN:
        return
    if not x_api_key or x_api_key != MOTHER_WRITE_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


def worldbook_write_auth(x_api_key: Optional[str]) -> None:
    if not WORLDBOOK_WRITE_TOKEN:
        return
    if not x_api_key or x_api_key != WORLDBOOK_WRITE_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


def j_write_auth(x_api_key: Optional[str]) -> None:
    if not J_WRITE_TOKEN:
        return
    if not x_api_key or x_api_key != J_WRITE_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


def worldbook_revision_conflict_detail(
    exc: WorldBookRevisionConflictError,
) -> dict[str, str]:
    return {
        "code": exc.code,
        "message": str(exc),
        "expected_revision": exc.expected_revision,
        "current_revision": exc.current_revision,
    }


def j_revision_conflict_detail(exc: JRevisionConflictError) -> dict[str, str]:
    return {
        "code": exc.code,
        "message": str(exc),
        "expected_revision": exc.expected_revision,
        "current_revision": exc.current_revision,
    }


def j_validation_error_detail(exc: JValidationError) -> dict[str, Any]:
    return {
        "code": exc.code,
        "operation_index": exc.operation_index,
        "item_id": exc.item_id,
        "field": exc.field,
        "message": str(exc),
    }


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


class MemoryRouteReq(BaseModel):
    query: str
    mode: Optional[str] = None
    task_hint: Optional[str] = None
    limit: int = 8


class MotherMemoryUpdateReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: str
    operations: List[dict[str, Any]]
    actor: Optional[str] = None


class WorldBookUpdateReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: str
    operations: List[dict[str, Any]]
    actor: Optional[str] = None


class JUpdateReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: str
    operations: List[dict[str, Any]]
    actor: Optional[str] = None


class MemoryCandidateStatusReq(BaseModel):
    status: str


class PromoteMemoryCandidateReq(BaseModel):
    candidate_ids: List[int]
    title: Optional[str] = None
    content: Optional[str] = None
    evidence: Optional[str] = None
    domain: Optional[str] = None
    function: Optional[str] = None
    primary_mother: Optional[str] = None
    secondary_mother: Optional[str] = None
    topic_key: Optional[str] = None
    layer_role: Optional[str] = None
    canonical_ref: Optional[str] = None
    importance: Optional[int] = None
    confidence: Optional[str] = None
    explicitness: Optional[str] = "edited_by_human"
    reviewer: Optional[str] = "human"
    reviewed_at: Optional[str] = None
    expires_at: Optional[str] = None
    review_after: Optional[str] = None
    superseded_by_item_id: Optional[int] = None
    metadata_json: Optional[Any] = None


class UpdateReviewedMemoryItemReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = None
    content: Optional[str] = None
    evidence: Optional[str] = None
    topic_key: Optional[str] = None
    layer_role: Optional[str] = None
    canonical_ref: Optional[str] = None
    importance: Optional[int] = None
    confidence: Optional[str] = None
    explicitness: Optional[str] = None
    expires_at: Optional[str] = None
    review_after: Optional[str] = None
    metadata_json: Optional[Any] = None


class UpdateReviewedMemoryStatusReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    superseded_by_item_id: Optional[int] = None


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
MEMORY_CANDIDATE_STATUSES = {
    "candidate",
    "accepted",
    "rejected",
    "deferred",
    "merged",
    "superseded",
    "promoted",
}
REVIEWED_MEMORY_STATUSES = {"active", "archived", "superseded"}


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
        or list_weekly_memory_candidates is None
    ):
        raise HTTPException(status_code=501, detail="Summaries are only implemented for the sqlite backend")


def _ensure_reviewed_memory_supported() -> None:
    if (
        SEARCH_BACKEND != "sqlite"
        or get_reviewed_memory_by_message is None
        or list_reviewed_memory_items is None
        or promote_memory_candidate is None
        or update_memory_candidate_status is None
        or update_reviewed_memory_item is None
        or update_reviewed_memory_status is None
    ):
        raise HTTPException(status_code=501, detail="Reviewed memory is only implemented for the sqlite backend")


def _ensure_core_anchors_supported() -> None:
    if SEARCH_BACKEND != "sqlite" or list_core_anchors is None:
        raise HTTPException(status_code=501, detail="Core anchors are only implemented for the sqlite backend")


def _ensure_mother_memory_supported() -> None:
    if (
        SEARCH_BACKEND != "sqlite"
        or list_mother_toc is None
        or get_mother_section is None
        or get_mother_source_info is None
        or search_mother_sections is None
        or route_mother_memory is None
        or apply_mother_memory_update is None
        or preview_mother_memory_update is None
    ):
        raise HTTPException(status_code=501, detail="Mother memory is only implemented for the sqlite backend")


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


@app.get("/memory_week", response_class=HTMLResponse)
def memory_week_page():
    return (STATIC_DIR / "memory_week.html").read_text(encoding="utf-8")


@app.get("/reviewed_memory", response_class=HTMLResponse)
def reviewed_memory_page():
    return (STATIC_DIR / "reviewed_memory.html").read_text(encoding="utf-8")


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


@app.get("/weekly_memory_candidates")
def api_list_weekly_memory_candidates(
    start_date: str,
    end_date: str,
    status: Optional[str] = "candidate",
    domain: Optional[str] = None,
    function: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100,
    raw_limit: int = 1000,
    x_api_key: Optional[str] = Header(default=None),
):
    auth(x_api_key)
    _ensure_summaries_supported()
    if limit < 1 or limit > 300:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 300")
    if raw_limit < 1 or raw_limit > 2000:
        raise HTTPException(status_code=400, detail="raw_limit must be between 1 and 2000")
    weekly = list_weekly_memory_candidates(
        start_date=start_date,
        end_date=end_date,
        status=status,
        domain=domain,
        function=function,
        q=q,
        limit=limit,
        raw_limit=raw_limit,
    )
    return {
        "start_date": start_date,
        "end_date": end_date,
        "status": status,
        "domain": domain,
        "function": function,
        "q": q,
        **weekly,
    }


@app.post("/memory_candidates/{candidate_id}/status")
def api_update_memory_candidate_status(
    candidate_id: int,
    req: MemoryCandidateStatusReq,
    x_api_key: Optional[str] = Header(default=None),
):
    auth(x_api_key)
    _ensure_reviewed_memory_supported()
    _validate_choice("status", req.status, MEMORY_CANDIDATE_STATUSES)
    candidate = update_memory_candidate_status(candidate_id, req.status)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Memory candidate not found")
    return {"candidate": candidate}


@app.post("/memory_candidates/promote")
def api_promote_memory_candidate(
    req: PromoteMemoryCandidateReq,
    x_api_key: Optional[str] = Header(default=None),
):
    auth(x_api_key)
    _ensure_reviewed_memory_supported()
    if not req.candidate_ids:
        raise HTTPException(status_code=400, detail="candidate_ids must not be empty")
    try:
        item = promote_memory_candidate(
            candidate_ids=req.candidate_ids,
            title=req.title,
            content=req.content,
            evidence=req.evidence,
            domain=req.domain,
            function=req.function,
            primary_mother=req.primary_mother,
            secondary_mother=req.secondary_mother,
            topic_key=req.topic_key,
            layer_role=req.layer_role,
            canonical_ref=req.canonical_ref,
            importance=req.importance,
            confidence=req.confidence,
            explicitness=req.explicitness,
            reviewer=req.reviewer,
            reviewed_at=req.reviewed_at,
            expires_at=req.expires_at,
            review_after=req.review_after,
            superseded_by_item_id=req.superseded_by_item_id,
            metadata_json=req.metadata_json,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"item": item}


@app.patch("/reviewed_memory_items/{item_id}")
def api_update_reviewed_memory_item(
    item_id: int,
    req: UpdateReviewedMemoryItemReq,
    x_api_key: Optional[str] = Header(default=None),
):
    auth(x_api_key)
    _ensure_reviewed_memory_supported()
    updates = req.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="At least one field is required")
    for field in ("title", "content"):
        if field in updates and (
            updates[field] is None or not str(updates[field]).strip()
        ):
            raise HTTPException(status_code=400, detail=f"{field} must not be empty")
    if "importance" in updates and updates["importance"] is not None:
        if updates["importance"] < 1 or updates["importance"] > 5:
            raise HTTPException(status_code=400, detail="importance must be between 1 and 5")
    try:
        item = update_reviewed_memory_item(item_id, updates)
    except ReviewedMemoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"item": item}


@app.post("/reviewed_memory_items/{item_id}/status")
def api_update_reviewed_memory_status(
    item_id: int,
    req: UpdateReviewedMemoryStatusReq,
    x_api_key: Optional[str] = Header(default=None),
):
    auth(x_api_key)
    _ensure_reviewed_memory_supported()
    _validate_choice("status", req.status, REVIEWED_MEMORY_STATUSES)
    if req.status == "superseded" and req.superseded_by_item_id is None:
        raise HTTPException(
            status_code=400,
            detail="superseded_by_item_id is required when status is superseded",
        )
    if req.status != "superseded" and req.superseded_by_item_id is not None:
        raise HTTPException(
            status_code=400,
            detail="superseded_by_item_id is only valid when status is superseded",
        )
    try:
        item = update_reviewed_memory_status(
            item_id,
            req.status,
            req.superseded_by_item_id,
        )
    except ReviewedMemoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReviewedMemoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"item": item}


@app.get("/reviewed_memory_items")
def api_list_reviewed_memory_items(
    id: Optional[int] = None,
    status: Optional[str] = "active",
    domain: Optional[str] = None,
    function: Optional[str] = None,
    primary_mother: Optional[str] = None,
    secondary_mother: Optional[str] = None,
    topic_key: Optional[str] = None,
    layer_role: Optional[str] = None,
    canonical_ref: Optional[str] = None,
    explicitness: Optional[str] = None,
    q: Optional[str] = None,
    include_expired: bool = False,
    include_sources: bool = False,
    limit: int = 50,
    x_api_key: Optional[str] = Header(default=None),
):
    auth(x_api_key)
    _ensure_reviewed_memory_supported()
    if status:
        _validate_choice("status", status, REVIEWED_MEMORY_STATUSES)
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    return {
        "id": id,
        "status": status,
        "domain": domain,
        "function": function,
        "primary_mother": primary_mother,
        "secondary_mother": secondary_mother,
        "topic_key": topic_key,
        "layer_role": layer_role,
        "canonical_ref": canonical_ref,
        "explicitness": explicitness,
        "q": q,
        "include_expired": include_expired,
        "include_sources": include_sources,
        "results": list_reviewed_memory_items(
            item_id=id,
            status=status,
            domain=domain,
            function=function,
            primary_mother=primary_mother,
            secondary_mother=secondary_mother,
            topic_key=topic_key,
            layer_role=layer_role,
            canonical_ref=canonical_ref,
            explicitness=explicitness,
            q=q,
            include_expired=include_expired,
            include_sources=include_sources,
            limit=limit,
        ),
    }


@app.get("/reviewed_memory/by_message")
def api_get_reviewed_memory_by_message(
    message_pk: Optional[int] = None,
    message_id: Optional[str] = None,
    status: Optional[str] = "active",
    include_expired: bool = False,
    limit: int = 50,
    x_api_key: Optional[str] = Header(default=None),
):
    auth(x_api_key)
    _ensure_reviewed_memory_supported()
    if status:
        _validate_choice("status", status, REVIEWED_MEMORY_STATUSES)
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    if message_pk is None and not message_id:
        raise HTTPException(status_code=400, detail="message_pk or message_id is required")
    return get_reviewed_memory_by_message(
        message_pk=message_pk,
        message_id=message_id,
        status=status,
        include_expired=include_expired,
        limit=limit,
    )


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


@app.get("/memory/source")
def api_memory_source(x_api_key: Optional[str] = Header(default=None)):
    auth(x_api_key)
    _ensure_mother_memory_supported()
    return get_mother_source_info()


@app.post("/memory/updates/preview")
def api_preview_mother_memory_update(
    req: MotherMemoryUpdateReq,
    x_api_key: Optional[str] = Header(default=None),
):
    mother_write_auth(x_api_key)
    _ensure_mother_memory_supported()
    if not req.operations:
        raise HTTPException(status_code=400, detail="operations must not be empty")
    if len(req.operations) > 100:
        raise HTTPException(status_code=400, detail="operations must contain at most 100 items")
    try:
        return preview_mother_memory_update(
            expected_revision=req.expected_revision,
            operations=req.operations,
        )
    except MotherMemoryRevisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/memory/updates/apply")
def api_apply_mother_memory_update(
    req: MotherMemoryUpdateReq,
    x_api_key: Optional[str] = Header(default=None),
):
    mother_write_auth(x_api_key)
    _ensure_mother_memory_supported()
    if not req.operations:
        raise HTTPException(status_code=400, detail="operations must not be empty")
    if len(req.operations) > 100:
        raise HTTPException(status_code=400, detail="operations must contain at most 100 items")
    try:
        return apply_mother_memory_update(
            expected_revision=req.expected_revision,
            operations=req.operations,
            actor=req.actor,
        )
    except MotherMemoryRevisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/memory/toc")
def api_memory_toc(x_api_key: Optional[str] = Header(default=None)):
    auth(x_api_key)
    _ensure_mother_memory_supported()
    return {"results": list_mother_toc()}


@app.get("/memory/section")
def api_memory_section(
    path: str,
    include_children: bool = False,
    x_api_key: Optional[str] = Header(default=None),
):
    auth(x_api_key)
    _ensure_mother_memory_supported()
    section = get_mother_section(path, include_children=include_children)
    if section is None:
        raise HTTPException(status_code=404, detail="Mother memory section not found")
    return {
        "path": path,
        "include_children": include_children,
        "section": section,
    }


@app.get("/memory/search")
def api_memory_search(
    q: str,
    scope: Optional[str] = None,
    limit: int = 20,
    x_api_key: Optional[str] = Header(default=None),
):
    auth(x_api_key)
    _ensure_mother_memory_supported()
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    return {
        "q": q,
        "scope": scope,
        "results": search_mother_sections(q=q, scope=scope, limit=limit),
    }


@app.post("/memory/route")
def api_memory_route(
    req: MemoryRouteReq,
    x_api_key: Optional[str] = Header(default=None),
):
    auth(x_api_key)
    _ensure_mother_memory_supported()
    if req.limit < 1 or req.limit > 20:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 20")
    return route_mother_memory(
        query=req.query,
        mode=req.mode,
        task_hint=req.task_hint,
        limit=req.limit,
    )


@app.get("/j/source")
def api_j_source(x_api_key: Optional[str] = Header(default=None)):
    auth(x_api_key)
    try:
        return get_j_source_info()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/j/updates/preview")
def api_preview_j_update(
    req: JUpdateReq,
    x_api_key: Optional[str] = Header(default=None),
):
    j_write_auth(x_api_key)
    try:
        return preview_j_update(req.expected_revision, req.operations)
    except JRevisionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=j_revision_conflict_detail(exc),
        ) from exc
    except JValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail=j_validation_error_detail(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/j/updates/apply")
def api_apply_j_update(
    req: JUpdateReq,
    x_api_key: Optional[str] = Header(default=None),
):
    j_write_auth(x_api_key)
    try:
        return apply_j_update(
            req.expected_revision,
            req.operations,
            actor=req.actor,
        )
    except JRevisionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=j_revision_conflict_detail(exc),
        ) from exc
    except JValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail=j_validation_error_detail(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/worldbook/source")
def api_worldbook_source(x_api_key: Optional[str] = Header(default=None)):
    auth(x_api_key)
    try:
        return get_worldbook_source_info()
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/worldbook/entries")
def api_worldbook_entries(
    q: Optional[str] = None,
    enabled: Optional[bool] = None,
    limit: int = 100,
    x_api_key: Optional[str] = Header(default=None),
):
    auth(x_api_key)
    try:
        return list_worldbook_entries(q=q, enabled=enabled, limit=limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/worldbook/updates/preview")
def api_preview_worldbook_update(
    req: WorldBookUpdateReq,
    x_api_key: Optional[str] = Header(default=None),
):
    worldbook_write_auth(x_api_key)
    try:
        return preview_worldbook_update(req.expected_revision, req.operations)
    except WorldBookRevisionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=worldbook_revision_conflict_detail(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/worldbook/updates/apply")
def api_apply_worldbook_update(
    req: WorldBookUpdateReq,
    x_api_key: Optional[str] = Header(default=None),
):
    worldbook_write_auth(x_api_key)
    try:
        return apply_worldbook_update(
            req.expected_revision,
            req.operations,
            actor=req.actor,
        )
    except WorldBookRevisionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=worldbook_revision_conflict_detail(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/worldbook/rebuild")
def api_rebuild_worldbook(x_api_key: Optional[str] = Header(default=None)):
    worldbook_write_auth(x_api_key)
    try:
        return rebuild_merged_worldbook()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
