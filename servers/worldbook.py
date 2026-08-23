from __future__ import annotations

import json
import os
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from source_write import (
        atomic_replace_with_backup,
        atomic_write_text,
        read_utf8_text,
        text_revision,
        unified_text_diff,
    )
except ModuleNotFoundError as exc:
    if exc.name != "source_write":
        raise
    from servers.source_write import (
        atomic_replace_with_backup,
        atomic_write_text,
        read_utf8_text,
        text_revision,
        unified_text_diff,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_WORLDBOOK_DIR = PROJECT_ROOT / "world_book"
SIBLING_WORLDBOOK_DIR = PROJECT_ROOT.parent / "chat-proxy" / "world_book"
DEFAULT_WORLDBOOK_DIR = (
    LOCAL_WORLDBOOK_DIR
    if LOCAL_WORLDBOOK_DIR.exists()
    else SIBLING_WORLDBOOK_DIR
)
DEFAULT_SOURCE_FILES = (
    "memory_system.json",
    "Expansion_Pack_v2.json",
    "Recent_Updates.json",
)
DEFAULT_UPDATE_FILE = "Recent_Updates.json"
DEFAULT_MERGED_FILE = "World_Book_Merged.json"
ENTRY_FIELDS = {
    "id",
    "name",
    "enabled",
    "priority",
    "position",
    "content",
    "injectDepth",
    "role",
    "keywords",
    "useRegex",
    "caseSensitive",
    "scanDepth",
    "constantActive",
}
WORLDBOOK_WRITE_LOCK = threading.Lock()


class WorldBookRevisionConflictError(ValueError):
    pass


def _env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    return values or default


def resolve_worldbook_dir(directory: str | Path | None = None) -> Path:
    raw = directory or os.getenv("KMLOG_WORLDBOOK_DIR", "") or DEFAULT_WORLDBOOK_DIR
    return Path(raw).expanduser().resolve()


def source_filenames() -> tuple[str, ...]:
    return _env_csv("KMLOG_WORLDBOOK_SOURCES", DEFAULT_SOURCE_FILES)


def update_filename() -> str:
    return os.getenv("KMLOG_WORLDBOOK_UPDATE_FILE", DEFAULT_UPDATE_FILE).strip() or DEFAULT_UPDATE_FILE


def merged_filename() -> str:
    return os.getenv("KMLOG_WORLDBOOK_MERGED_FILE", DEFAULT_MERGED_FILE).strip() or DEFAULT_MERGED_FILE


def _safe_child(directory: Path, filename: str) -> Path:
    if not filename or Path(filename).name != filename:
        raise ValueError(f"World Book filename must be a basename: {filename}")
    path = (directory / filename).resolve()
    if path.parent != directory:
        raise ValueError(f"World Book path escapes configured directory: {filename}")
    return path


def _validate_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError("World Book entries must be JSON objects")
    unknown = set(entry) - ENTRY_FIELDS
    if unknown:
        raise ValueError(f"Unsupported World Book entry fields: {sorted(unknown)}")
    for field in ("id", "name", "content"):
        if not isinstance(entry.get(field), str) or not entry[field].strip():
            raise ValueError(f"World Book entry requires non-empty {field}")
    if not isinstance(entry.get("keywords", []), list) or not all(
        isinstance(item, str) for item in entry.get("keywords", [])
    ):
        raise ValueError("World Book entry keywords must be a list of strings")
    return entry


def validate_lorebook(document: Any, source_name: str = "worldbook") -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError(f"{source_name} must contain a JSON object")
    if document.get("type") != "lorebook":
        raise ValueError(f"{source_name} type must be lorebook")
    data = document.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise ValueError(f"{source_name} data.entries must be a list")
    ids: set[str] = set()
    names: set[str] = set()
    for entry in data["entries"]:
        _validate_entry(entry)
        entry_id = entry["id"]
        name = entry["name"]
        if entry_id in ids:
            raise ValueError(f"Duplicate World Book entry id in {source_name}: {entry_id}")
        if name in names:
            raise ValueError(f"Duplicate World Book entry name in {source_name}: {name}")
        ids.add(entry_id)
        names.add(name)
    return document


def load_lorebook(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        raise RuntimeError(f"World Book source not found: {path}")
    text = read_utf8_text(path)
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid World Book JSON in {path.name}: {exc}") from exc
    return validate_lorebook(document, path.name), text


def _serialize_lorebook(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def _merged_document(
    directory: Path,
    replacement: tuple[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    seen_ids: dict[str, str] = {}
    seen_names: dict[str, str] = {}
    source_ids: list[str] = []
    for filename in source_filenames():
        if replacement and replacement[0] == filename:
            document = replacement[1]
        else:
            document, _ = load_lorebook(_safe_child(directory, filename))
        data = document["data"]
        source_ids.append(str(data.get("id") or filename))
        manifest.append(
            {
                "file": filename,
                "id": data.get("id"),
                "name": data.get("name"),
                "entry_count": len(data["entries"]),
            }
        )
        for entry in data["entries"]:
            entry_id = entry["id"]
            name = entry["name"]
            if entry_id in seen_ids:
                raise ValueError(
                    f"Duplicate World Book entry id {entry_id} in {seen_ids[entry_id]} and {filename}"
                )
            if name in seen_names:
                raise ValueError(
                    f"Duplicate World Book entry name {name} in {seen_names[name]} and {filename}"
                )
            seen_ids[entry_id] = filename
            seen_names[name] = filename
            entries.append(deepcopy(entry))

    merged_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(source_ids)))
    document = {
        "version": 1,
        "type": "lorebook",
        "data": {
            "id": merged_id,
            "name": "KMLog Unified World Book",
            "description": "Generated from configured KMLog World Book sources. Edit Recent_Updates.json through the revision-protected update workflow.",
            "enabled": True,
            "entries": entries,
        },
    }
    validate_lorebook(document, "merged World Book")
    return document, manifest


def get_worldbook_source_info(directory: str | Path | None = None) -> dict[str, Any]:
    base = resolve_worldbook_dir(directory)
    path = _safe_child(base, update_filename())
    document, text = load_lorebook(path)
    return {
        "source_file": str(path),
        "revision": text_revision(text),
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "entry_count": len(document["data"]["entries"]),
        "merged_file": str(_safe_child(base, merged_filename())),
        "source_files": list(source_filenames()),
    }


def list_worldbook_entries(
    q: str | None = None,
    enabled: bool | None = None,
    limit: int = 100,
    directory: str | Path | None = None,
) -> dict[str, Any]:
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")
    base = resolve_worldbook_dir(directory)
    document, manifest = _merged_document(base)
    query = (q or "").strip().casefold()
    results = []
    for entry in document["data"]["entries"]:
        if enabled is not None and bool(entry.get("enabled", True)) != enabled:
            continue
        haystack = "\n".join(
            [entry.get("name", ""), entry.get("content", ""), *entry.get("keywords", [])]
        ).casefold()
        if query and query not in haystack:
            continue
        results.append(entry)
        if len(results) >= limit:
            break
    return {
        "q": q,
        "enabled": enabled,
        "total_entries": len(document["data"]["entries"]),
        "sources": manifest,
        "results": results,
    }


def _apply_operations(document: dict[str, Any], operations: list[dict[str, Any]]) -> dict[str, Any]:
    if not operations:
        raise ValueError("operations must not be empty")
    if len(operations) > 100:
        raise ValueError("operations must contain at most 100 items")
    result = deepcopy(document)
    entries = result["data"]["entries"]
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("World Book operations must be JSON objects")
        op = str(operation.get("op") or "").strip()
        entry_id = str(operation.get("id") or "").strip()
        index = next((i for i, entry in enumerate(entries) if entry["id"] == entry_id), None)
        if op == "create_entry":
            if set(operation) != {"op", "entry"}:
                raise ValueError("create_entry accepts only op and entry")
            entry = deepcopy(_validate_entry(operation.get("entry")))
            if any(item["id"] == entry["id"] for item in entries):
                raise ValueError(f"World Book entry already exists: {entry['id']}")
            if any(item["name"] == entry["name"] for item in entries):
                raise ValueError(f"World Book entry name already exists: {entry['name']}")
            entries.append(entry)
            continue
        if op not in {"replace_entry", "patch_entry", "set_enabled"}:
            raise ValueError(f"Unsupported World Book operation: {op}")
        if not entry_id:
            raise ValueError(f"{op} requires id")
        if index is None:
            raise ValueError(f"World Book entry not found: {entry_id}")
        if op == "replace_entry":
            if set(operation) != {"op", "id", "entry"}:
                raise ValueError("replace_entry accepts only op, id, and entry")
            entry = deepcopy(_validate_entry(operation.get("entry")))
            if entry["id"] != entry_id:
                raise ValueError("replace_entry cannot change the entry id")
            entries[index] = entry
        elif op == "patch_entry":
            if set(operation) != {"op", "id", "changes"}:
                raise ValueError("patch_entry accepts only op, id, and changes")
            changes = operation.get("changes")
            if not isinstance(changes, dict) or not changes:
                raise ValueError("patch_entry requires non-empty changes")
            if "id" in changes:
                raise ValueError("patch_entry cannot change the entry id")
            unknown = set(changes) - ENTRY_FIELDS
            if unknown:
                raise ValueError(f"Unsupported World Book entry fields: {sorted(unknown)}")
            entries[index] = deepcopy({**entries[index], **changes})
            _validate_entry(entries[index])
        else:
            if set(operation) != {"op", "id", "enabled"} or not isinstance(
                operation.get("enabled"), bool
            ):
                raise ValueError("set_enabled requires op, id, and boolean enabled")
            entries[index]["enabled"] = operation["enabled"]
    return validate_lorebook(result, update_filename())


def preview_worldbook_update(
    expected_revision: str,
    operations: list[dict[str, Any]],
    directory: str | Path | None = None,
) -> dict[str, Any]:
    base = resolve_worldbook_dir(directory)
    filename = update_filename()
    path = _safe_child(base, filename)
    document, text = load_lorebook(path)
    revision = text_revision(text)
    if revision != expected_revision:
        raise WorldBookRevisionConflictError(
            f"World Book revision changed: expected {expected_revision}, current {revision}"
        )
    result = _apply_operations(document, operations)
    _merged_document(base, replacement=(filename, result))
    result_text = _serialize_lorebook(result)
    return {
        "valid": True,
        "source_file": str(path),
        "base_revision": revision,
        "result_revision": text_revision(result_text),
        "changed_entry_ids": list(
            dict.fromkeys(
                str(item.get("id") or item.get("entry", {}).get("id") or "")
                for item in operations
            )
        ),
        "diff": unified_text_diff(path, text, result_text),
        "warnings": [],
    }


def rebuild_merged_worldbook(directory: str | Path | None = None) -> dict[str, Any]:
    base = resolve_worldbook_dir(directory)
    document, manifest = _merged_document(base)
    path = _safe_child(base, merged_filename())
    text = _serialize_lorebook(document)
    atomic_write_text(path, text)
    return {
        "merged_file": str(path),
        "revision": text_revision(text),
        "entry_count": len(document["data"]["entries"]),
        "sources": manifest,
    }


def apply_worldbook_update(
    expected_revision: str,
    operations: list[dict[str, Any]],
    actor: str | None = None,
    directory: str | Path | None = None,
) -> dict[str, Any]:
    base = resolve_worldbook_dir(directory)
    filename = update_filename()
    path = _safe_child(base, filename)
    with WORLDBOOK_WRITE_LOCK:
        preview = preview_worldbook_update(expected_revision, operations, base)
        document, text = load_lorebook(path)
        current_revision = text_revision(text)
        if current_revision != expected_revision:
            raise WorldBookRevisionConflictError(
                f"World Book revision changed: expected {expected_revision}, current {current_revision}"
            )
        result = _apply_operations(document, operations)
        backup_path = atomic_replace_with_backup(path, _serialize_lorebook(result))
        merged = rebuild_merged_worldbook(base)
        return {
            **preview,
            "applied": True,
            "actor": actor,
            "before_revision": current_revision,
            "after_revision": preview["result_revision"],
            "backup_file": str(backup_path),
            "merged": merged,
        }
