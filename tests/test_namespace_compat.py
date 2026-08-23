import asyncio

import pytest

from servers.namespace_compat import (
    NamespaceCompatibleFastMCP,
    normalize_tool_name,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("health", "health"),
        ("kmlog_search.health", "health"),
        ("kmlog-search.health", "health"),
        ("other.health", "other.health"),
        ("kmlog_search.unknown", "kmlog_search.unknown"),
    ],
)
def test_normalize_tool_name_is_restricted(name, expected):
    assert normalize_tool_name(
        name,
        known_names={"health"},
        allowed_prefixes={"kmlog_search", "kmlog-search"},
    ) == expected


def test_namespaced_tool_dispatches_to_registered_tool():
    mcp = NamespaceCompatibleFastMCP(
        "test",
        allowed_tool_prefixes={"kmlog_search"},
    )

    @mcp.tool
    def echo(text: str) -> dict:
        return {"text": text}

    plain = asyncio.run(mcp.call_tool("echo", {"text": "plain"}))
    namespaced = asyncio.run(
        mcp.call_tool("kmlog_search.echo", {"text": "namespaced"})
    )

    assert plain.structured_content == {"text": "plain"}
    assert namespaced.structured_content == {"text": "namespaced"}


def test_unknown_namespaced_tool_stays_unknown():
    mcp = NamespaceCompatibleFastMCP(
        "test",
        allowed_tool_prefixes={"kmlog_search"},
    )

    with pytest.raises(Exception, match="Unknown tool"):
        asyncio.run(mcp.call_tool("kmlog_search.unknown", {}))
