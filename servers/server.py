#!/usr/bin/env python3
import os
import httpx
import sys
from fastmcp import FastMCP
from typing import Optional

# 从环境变量读取配置
API_BASE_URL = os.getenv("KMLOG_API_URL", "http://127.0.0.1:8013")
API_KEY = os.getenv("KMLOG_API_KEY", "")

if not API_KEY:
    print("Warning: KMLOG_API_KEY not set", file=sys.stderr)

mcp = FastMCP("KMLog Search")

async def _call_api(endpoint: str, payload: dict) -> dict:
    """内部函数：调用 KMLog API"""
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": API_KEY
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{API_BASE_URL}{endpoint}", json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()

@mcp.tool
async def search_kmlog(query: str, limit: int = 10, mode: str = "auto") -> dict:
    """
    搜索 KMLog 消息内容（全文搜索）
    
    参数:
        query: 搜索关键词
        limit: 返回结果数量，默认 10
        mode: 搜索模式，可选 "auto", "phrase", "tokens"，默认 "auto"
    """
    payload = {"query": query, "limit": limit, "mode": mode}
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
async def kmlog_health() -> dict:
    """检查 KMLog API 服务健康状态"""
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{API_BASE_URL}/healthz")
        resp.raise_for_status()
        return {"status": "healthy", "api": API_BASE_URL}

if __name__ == "__main__":
    mcp.run(transport='sse', host='127.0.0.1', port=8002)
