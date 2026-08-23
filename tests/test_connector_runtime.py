import asyncio

from servers import connector_runtime, server


def test_connector_info_describes_current_registry(monkeypatch):
    monkeypatch.setattr(connector_runtime, "BUILD_SHA", "test-build")

    info = asyncio.run(connector_runtime.get_connector_info(server.mcp))

    assert info["build_sha"] == "test-build"
    assert info["instance_id"]
    assert info["started_at"].endswith("+00:00")
    assert info["registry_version"].startswith("sha256:")
    assert "connector_info" in info["registered_bare_names"]
    assert "get_j_source" in info["registered_bare_names"]
    assert "kmlog_search.get_j_source" in info["registered_namespaced_names"]
    assert info["accepted_namespaced_names"] == info["registered_namespaced_names"]
