import os
from typing import Optional, Any, Dict
import httpx

# If you're already using FastMCP, this should match your health MCP style.
# pip install fastmcp
from fastmcp import FastMCP

BASE_URL = os.getenv("KMLOG_SEARCH_BASE_URL", "https://wm.511388.xyz").rstrip("/")
API_TOKEN = os.getenv("KMLOG_SEARCH_API_TOKEN", "")  # same as SEARCH_API_TOKEN on server

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
async def get_conversation_summary(conversation_id: str) -> Dict[str, Any]:
    """Read the rolling summary for one conversation_id."""
    return await _get("/conversation_summary", {"conversation_id": conversation_id})

if __name__ == "__main__":
    # Run MCP server (stdio)
    mcp.run()
