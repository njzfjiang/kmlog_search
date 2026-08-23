from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any


STARTED_AT = datetime.now(timezone.utc).isoformat()
INSTANCE_ID = str(uuid.uuid4())
BUILD_SHA: str | None = None


async def get_connector_info(mcp: Any) -> dict[str, Any]:
    tools = sorted(await mcp.list_tools(), key=lambda tool: tool.name)
    bare_names = [tool.name for tool in tools]
    prefixes = sorted(getattr(mcp, "_allowed_tool_prefixes", ()))
    registry_payload = [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
            "output_schema": tool.output_schema,
        }
        for tool in tools
    ]
    encoded = json.dumps(
        registry_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    namespaced_names = [
        f"{prefix}.{name}" for prefix in prefixes for name in bare_names
    ]
    build_sha = BUILD_SHA or os.getenv("KMLOG_BUILD_SHA", "").strip() or "unknown"
    return {
        "build_sha": build_sha,
        "started_at": STARTED_AT,
        "instance_id": INSTANCE_ID,
        "registry_version": f"sha256:{hashlib.sha256(encoded).hexdigest()}",
        "registered_bare_names": bare_names,
        "registered_namespaced_names": namespaced_names,
        "accepted_namespaced_names": namespaced_names,
        "namespace_prefixes": prefixes,
    }
