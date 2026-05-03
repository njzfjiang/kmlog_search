#!/usr/bin/env python3
import os
import httpx
import sys
from fastmcp import FastMCP
from typing import Optional, List

# 从环境变量读取配置
API_BASE_URL = os.getenv("KMLOG_API_URL", "http://127.0.0.1:8013")
API_KEY = os.getenv("KMLOG_API_KEY", "")

if not API_KEY:
    print("Warning: KMLOG_API_KEY not set", file=sys.stderr)

mcp = FastMCP("KMLog Search")

async def _call_api(endpoint: str, payload: dict, method: str = "POST") -> dict:
    """内部函数：调用 KMLog API"""
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": API_KEY
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        if method.upper() == "GET":
            resp = await client.get(f"{API_BASE_URL}{endpoint}", params=payload, headers=headers)
        else:
            resp = await client.post(f"{API_BASE_URL}{endpoint}", json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()

@mcp.tool
async def search_kmlog(
    query: str,
    limit: int = 10,
    mode: str = "auto",
    kinds: Optional[List[str]] = None,
    after: Optional[str] = None,
    before: Optional[str] = None,
) -> dict:
    """
    搜索 KMLog 消息内容（全文搜索）
    
    参数:
        query: 搜索关键词
        limit: 返回结果数量，默认 10
        mode: 搜索模式，可选 "auto", "phrase", "tokens"，默认 "auto"
        kinds: 可选，消息类型过滤，如 ["chat"]、["summary"] 或 ["chat", "summary"]
        after: 可选，只搜索这个时间之后的消息，如 "2026-01-01"
        before: 可选，只搜索这个时间之前的消息，如 "2026-05-01"
    """
    payload = {"query": query, "limit": limit, "mode": mode}
    if kinds:
        payload["kinds"] = kinds
    if after:
        payload["after"] = after
    if before:
        payload["before"] = before
    result = await _call_api("/search", payload)
    return result

@mcp.tool
async def search_kmlog_by_date(
    start_date: str,
    end_date: str,
    role: Optional[str] = None,
    limit: int = 20
) -> dict:
    """
    按日期范围搜索 KMLog 消息
    
    参数:
        start_date: 开始日期，格式 YYYY-MM-DD 或 ISO 时间戳
        end_date: 结束日期
        role: 可选，消息角色（如 "user", "assistant"）
        limit: 返回结果数量，默认 20
    """
    payload = {
        "start_date": start_date,
        "end_date": end_date,
        "limit": limit
    }
    if role:
        payload["role"] = role
    result = await _call_api("/search_by_date", payload)
    return result

@mcp.tool
async def get_wish(
    wish_id: Optional[int] = None,
    owner: Optional[str] = None,
    scope: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 50
) -> dict:
    """
    读取愿望列表，可按 id/keyword/owner/scope/status 过滤。

    参数:
        wish_id: 可选，愿望 ID
        q: 可选，在 text/tags 里搜索的关键词
        owner: 可选，"Mei"、"Kai" 或 "Shared"
        scope: 可选，"care"、"work"、"romance"、"play" 或 "misc"
        status: 可选，"open"、"done"、"stale" 或 "archived"
        limit: 返回结果数量，默认 50
    """
    payload = {"limit": limit}
    if wish_id is not None:
        payload["id"] = wish_id
    if owner:
        payload["owner"] = owner
    if scope:
        payload["scope"] = scope
    if status:
        payload["status"] = status
    if q:
        payload["q"] = q
    return await _call_api("/wish", payload, method="GET")

@mcp.tool
async def post_wish(
    owner: str,
    scope: str,
    text: str,
    status: str = "open",
    priority: int = 3,
    tags: str = "",
    source: str = "manual",
    created_at: Optional[str] = None
) -> dict:
    """
    创建一条愿望。

    参数:
        owner: "Mei"、"Kai" 或 "Shared"
        scope: "care"、"work"、"romance"、"play" 或 "misc"
        text: 愿望内容（短）
        status: "open"、"done"、"stale" 或 "archived"，默认 "open"
        priority: 1-5，默认 3
        tags: 逗号分隔标签，如 "foo,bar"
        source: "manual" 或 "agent_suggest"，默认 "manual"
        created_at: 可选 ISO 时间戳，不传则由 API 生成
    """
    payload = {
        "owner": owner,
        "scope": scope,
        "text": text,
        "status": status,
        "priority": priority,
        "tags": tags,
        "source": source,
    }
    if created_at:
        payload["created_at"] = created_at
    return await _call_api("/wish", payload)

@mcp.tool
async def complete_wish(wish_id: int) -> dict:
    """
    将一条愿望标记为完成。

    参数:
        wish_id: 愿望 ID
    """
    return await _call_api(f"/complete_wish/{wish_id}", {})

@mcp.tool
async def set_wish_status(wish_id: int, status: str) -> dict:
    """
    更新一条愿望的状态。

    参数:
        wish_id: 愿望 ID
        status: "open"、"done"、"stale" 或 "archived"
    """
    return await _call_api(f"/wish/{wish_id}/status", {"status": status})

@mcp.tool
async def kmlog_health() -> dict:
    """检查 KMLog API 服务健康状态"""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{API_BASE_URL}/healthz")
        resp.raise_for_status()
        return {"status": "healthy", "api": API_BASE_URL}

if __name__ == "__main__":
    mcp.run(transport='sse', host='127.0.0.1', port=8002)
