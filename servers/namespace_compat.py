from __future__ import annotations

from collections.abc import Collection
from typing import Any

from fastmcp import FastMCP
from fastmcp.utilities.logging import get_logger


logger = get_logger(__name__)


def normalize_tool_name(
    name: str,
    known_names: Collection[str],
    allowed_prefixes: Collection[str],
) -> str:
    if name in known_names:
        return name

    prefix, separator, candidate = name.partition(".")
    if separator and prefix in allowed_prefixes and candidate in known_names:
        return candidate
    return name


class NamespaceCompatibleFastMCP(FastMCP):
    def __init__(
        self,
        *args: Any,
        allowed_tool_prefixes: Collection[str] = (),
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._allowed_tool_prefixes = frozenset(allowed_tool_prefixes)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        **kwargs: Any,
    ):
        logger.info("MCP tool dispatch: %s", name)
        known_names = {tool.name for tool in await self.list_tools()}
        normalized = normalize_tool_name(
            name,
            known_names,
            self._allowed_tool_prefixes,
        )
        if normalized != name:
            logger.info("Normalized MCP tool name: %s -> %s", name, normalized)
        return await super().call_tool(normalized, arguments, **kwargs)
