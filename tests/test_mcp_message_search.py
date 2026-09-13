import asyncio

from servers import mcp_search_supabase, server


def test_primary_mcp_search_requests_evidence_by_default(monkeypatch):
    captured = {}

    async def fake_call(endpoint, payload, method="POST", api_key=None):
        captured.update(endpoint=endpoint, payload=payload, method=method)
        return {"results": []}

    monkeypatch.setattr(server, "_call_api", fake_call)
    asyncio.run(server.search_kmlog("FISTA", evidence_terms=["optimization"]))

    assert captured["endpoint"] == "/search"
    assert captured["payload"]["include_evidence"] is True
    assert captured["payload"]["evidence_terms"] == ["optimization"]


def test_legacy_mcp_search_requests_evidence_by_default(monkeypatch):
    captured = {}

    async def fake_post(path, payload, api_token=None):
        captured.update(path=path, payload=payload)
        return {"results": []}

    monkeypatch.setattr(mcp_search_supabase, "_post", fake_post)
    asyncio.run(mcp_search_supabase.search_logs("FISTA"))

    assert captured["path"] == "/search"
    assert captured["payload"]["include_evidence"] is True


def test_complete_message_tools_use_numeric_result_id(monkeypatch):
    primary = {}
    legacy = {}

    async def fake_call(endpoint, payload, method="POST", api_key=None):
        primary.update(endpoint=endpoint, payload=payload, method=method)
        return {"message": {"id": 42, "content": "complete"}}

    async def fake_get(path, params=None):
        legacy.update(path=path, params=params)
        return {"message": {"id": 42, "content": "complete"}}

    monkeypatch.setattr(server, "_call_api", fake_call)
    monkeypatch.setattr(mcp_search_supabase, "_get", fake_get)

    assert asyncio.run(server.get_kmlog_message(42))["message"]["content"] == "complete"
    assert asyncio.run(mcp_search_supabase.get_kmlog_message(42))["message"]["content"] == "complete"
    assert primary == {"endpoint": "/messages/42", "payload": {}, "method": "GET"}
    assert legacy == {"path": "/messages/42", "params": None}
