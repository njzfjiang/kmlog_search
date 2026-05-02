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

async def _get(path: str) -> Dict[str, Any]:
    url = f"{BASE_URL}{path}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url)
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

if __name__ == "__main__":
    # Run MCP server (stdio)
    mcp.run()