import os
from pathlib import Path
from typing import Optional, Any, Dict
import httpx

# If you're already using FastMCP, this should match your health MCP style.
# pip install fastmcp
from fastmcp import FastMCP
try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

SERVER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SERVER_DIR.parent
if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(SERVER_DIR / ".env")

BASE_URL = (
    os.getenv("KMLOG_SEARCH_BASE_URL")
    or os.getenv("KMLOG_BASE_URL")
    or "https://wm.511388.xyz"
).rstrip("/")
API_TOKEN = (
    os.getenv("KMLOG_SEARCH_API_TOKEN")
    or os.getenv("KMLOG_API_KEY")
    or os.getenv("SEARCH_API_TOKEN")
    or ""
)

mcp = FastMCP("kmlog_search")

def _headers() -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if API_TOKEN:
        h["X-API-KEY"] = API_TOKEN
    return h

async def _post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{BASE_URL}{path}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(url, headers=_headers(), json=payload)
        r.raise_for_status()
        return r.json()

async def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"{BASE_URL}{path}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url, headers=_headers(), params=params)
        r.raise_for_status()
        return r.json()

@mcp.tool()
async def healthz() -> Dict[str, Any]:
    """Check if the KMLog Search API is alive."""
    return await _get("/healthz")

@mcp.tool()
async def search_logs(query: str, limit: int = 10) -> Dict[str, Any]:
    """
    Keyword search over Supabase messages (hybrid FTS + trigram).
    Use when the user explicitly asks to "回看/翻记录/查原话".
    """
    if not query.strip():
        return {"query": query, "results": []}
    return await _post("/search", {"query": query, "limit": int(limit)})

@mcp.tool()
async def search_logs_by_date(
    start_date: str,
    end_date: str,
    role: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """
    Date range search (optionally filtered by role).
    start_date/end_date: "YYYY-MM-DD" (or timestamps accepted by your backend).
    """
    payload = {
        "start_date": start_date,
        "end_date": end_date,
        "role": role,
        "limit": int(limit),
    }
    return await _post("/search_by_date", payload)

@mcp.tool()
async def get_daily_summary(date_key: str) -> Dict[str, Any]:
    """Read one complete daily summary by date_key, formatted as YYYY-MM-DD."""
    return await _get("/daily_summary", {"date_key": date_key})

@mcp.tool()
async def list_daily_summaries(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """List daily summaries, optionally filtered by date range and status."""
    params: Dict[str, Any] = {"limit": int(limit)}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    if status:
        params["status"] = status
    return await _get("/daily_summaries", params)

@mcp.tool()
async def get_daily_memory_candidates(
    date_key: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    status: Optional[str] = None,
    domain: Optional[str] = None,
    function: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Read suggested memory/summary candidates generated from daily summaries."""
    params: Dict[str, Any] = {"limit": int(limit)}
    if date_key:
        params["date_key"] = date_key
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    if status:
        params["status"] = status
    if domain:
        params["domain"] = domain
    if function:
        params["function"] = function
    if q:
        params["q"] = q
    return await _get("/daily_memory_candidates", params)

@mcp.tool()
async def get_weekly_memory_candidates(
    start_date: str,
    end_date: str,
    status: Optional[str] = "candidate",
    domain: Optional[str] = None,
    function: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100,
    raw_limit: int = 1000,
) -> Dict[str, Any]:
    """Read weekly memory candidates grouped by a conservative dedupe key."""
    params: Dict[str, Any] = {
        "start_date": start_date,
        "end_date": end_date,
        "limit": int(limit),
        "raw_limit": int(raw_limit),
    }
    if status is not None:
        params["status"] = status
    if domain:
        params["domain"] = domain
    if function:
        params["function"] = function
    if q:
        params["q"] = q
    return await _get("/weekly_memory_candidates", params)

@mcp.tool()
async def get_conversation_summary(conversation_id: str) -> Dict[str, Any]:
    """Read the rolling summary for one conversation_id."""
    return await _get("/conversation_summary", {"conversation_id": conversation_id})

@mcp.tool()
async def get_core_anchors(
    anchor_key: Optional[str] = None,
    function: Optional[str] = None,
    primary_mother: Optional[str] = None,
    secondary_mother: Optional[str] = None,
    status: Optional[str] = "active",
    q: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """Read Core Anchors for boot/context injection."""
    params: Dict[str, Any] = {"limit": int(limit)}
    if anchor_key:
        params["anchor_key"] = anchor_key
    if function:
        params["function"] = function
    if primary_mother:
        params["primary_mother"] = primary_mother
    if secondary_mother:
        params["secondary_mother"] = secondary_mother
    if status is not None:
        params["status"] = status
    if q:
        params["q"] = q
    return await _get("/core_anchors", params)

@mcp.tool()
async def get_mother_toc() -> Dict[str, Any]:
    """Read the mother markdown memory table of contents."""
    return await _get("/memory/toc")

@mcp.tool()
async def get_mother_section(path: str) -> Dict[str, Any]:
    """Read one mother markdown memory section by path, e.g. F.2."""
    return await _get("/memory/section", {"path": path})

@mcp.tool()
async def search_mother_memory(
    q: str,
    scope: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """Lightweight keyword search over the mother markdown memory file."""
    params: Dict[str, Any] = {"q": q, "limit": int(limit)}
    if scope:
        params["scope"] = scope
    return await _get("/memory/search", params)

@mcp.tool()
async def route_mother_memory(
    query: str,
    mode: Optional[str] = None,
    task_hint: Optional[str] = None,
    limit: int = 8,
) -> Dict[str, Any]:
    """Route a query to likely mother memory sections without injection."""
    payload: Dict[str, Any] = {"query": query, "limit": int(limit)}
    if mode:
        payload["mode"] = mode
    if task_hint:
        payload["task_hint"] = task_hint
    return await _post("/memory/route", payload)

if __name__ == "__main__":
    # Run MCP server (stdio)
    mcp.run()
