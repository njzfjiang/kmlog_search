import asyncio

import httpx

from servers import mcp_search_supabase, server


def _conflict_error() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://api/worldbook/updates/preview")
    response = httpx.Response(
        409,
        request=request,
        json={
            "detail": {
                "code": "REVISION_CONFLICT",
                "message": "revision changed",
                "expected_revision": "sha256:old",
                "current_revision": "sha256:new",
            }
        },
    )
    return httpx.HTTPStatusError(
        "conflict",
        request=request,
        response=response,
    )


def _validation_error() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://api/j/updates/preview")
    response = httpx.Response(
        400,
        request=request,
        json={
            "detail": {
                "code": "VALIDATION_ERROR",
                "operation_index": 0,
                "item_id": "J-TEST-001",
                "field": "created_at",
                "message": "created_at is immutable",
            }
        },
    )
    return httpx.HTTPStatusError(
        "validation failed",
        request=request,
        response=response,
    )


def test_primary_mcp_returns_structured_revision_conflict(monkeypatch):
    async def fake_call_api(*args, **kwargs):
        raise _conflict_error()

    monkeypatch.setattr(server, "_call_api", fake_call_api)

    result = asyncio.run(server._call_worldbook_write_api("/preview", {}))

    assert result == {
        "ok": False,
        "code": "REVISION_CONFLICT",
        "message": "revision changed",
        "expected_revision": "sha256:old",
        "current_revision": "sha256:new",
    }


def test_legacy_mcp_returns_structured_revision_conflict(monkeypatch):
    async def fake_post(*args, **kwargs):
        raise _conflict_error()

    monkeypatch.setattr(mcp_search_supabase, "_post", fake_post)

    result = asyncio.run(mcp_search_supabase._post_worldbook_write("/preview", {}))

    assert result["ok"] is False
    assert result["code"] == "REVISION_CONFLICT"
    assert result["current_revision"] == "sha256:new"


def test_primary_mcp_j_returns_structured_revision_conflict(monkeypatch):
    async def fake_call_api(*args, **kwargs):
        raise _conflict_error()

    monkeypatch.setattr(server, "_call_api", fake_call_api)

    result = asyncio.run(server._call_j_write_api("/j/updates/preview", {}))

    assert result["ok"] is False
    assert result["code"] == "REVISION_CONFLICT"
    assert result["current_revision"] == "sha256:new"


def test_legacy_mcp_j_returns_structured_revision_conflict(monkeypatch):
    async def fake_post(*args, **kwargs):
        raise _conflict_error()

    monkeypatch.setattr(mcp_search_supabase, "_post", fake_post)

    result = asyncio.run(mcp_search_supabase._post_j_write("/j/updates/preview", {}))

    assert result["ok"] is False
    assert result["code"] == "REVISION_CONFLICT"
    assert result["current_revision"] == "sha256:new"


def test_primary_mcp_j_returns_structured_validation_error(monkeypatch):
    async def fake_call_api(*args, **kwargs):
        raise _validation_error()

    monkeypatch.setattr(server, "_call_api", fake_call_api)

    result = asyncio.run(server._call_j_write_api("/j/updates/preview", {}))

    assert result == {
        "ok": False,
        "code": "VALIDATION_ERROR",
        "operation_index": 0,
        "item_id": "J-TEST-001",
        "field": "created_at",
        "message": "created_at is immutable",
    }


def test_legacy_mcp_j_returns_structured_validation_error(monkeypatch):
    async def fake_post(*args, **kwargs):
        raise _validation_error()

    monkeypatch.setattr(mcp_search_supabase, "_post", fake_post)

    result = asyncio.run(mcp_search_supabase._post_j_write("/j/updates/preview", {}))

    assert result["ok"] is False
    assert result["code"] == "VALIDATION_ERROR"
    assert result["field"] == "created_at"
    assert result["message"] == "created_at is immutable"
