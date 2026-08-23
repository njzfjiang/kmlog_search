from __future__ import annotations

import json
import os
import re
import threading
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    from source_write import (
        atomic_replace_with_backup,
        read_utf8_text,
        text_revision,
        unified_text_diff,
    )
except ModuleNotFoundError as exc:
    if exc.name != "source_write":
        raise
    from servers.source_write import (
        atomic_replace_with_backup,
        read_utf8_text,
        text_revision,
        unified_text_diff,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_J_SOURCE_FILE = PROJECT_ROOT / "recent" / "Recent Goals(Current).md"
J_WRITE_LOCK = threading.Lock()

ITEM_HEADING_RE = re.compile(
    r"^### \[(?P<id>J-[A-Za-z0-9._-]+)\] (?P<title>[^\r\n]+)\r?$",
    re.MULTILINE,
)
META_RE = re.compile(r"\A\s*<!-- j-item\s*\r?\n(?P<json>.*?)\r?\n-->\s*\r?\n?", re.DOTALL)
CONTAINER_LINE_RE = re.compile(r"^## (?:Active|Archive)\s*$")

METADATA_FIELD_ORDER = (
    "id",
    "owner",
    "area",
    "status",
    "created_at",
    "expires_at",
    "review_on",
    "archived_at",
    "archive_reason",
)
METADATA_FIELDS = set(METADATA_FIELD_ORDER)
PATCH_FIELDS = {"title", "body", "owner", "area", "expires_at", "review_on"}


class JRevisionConflictError(ValueError):
    code = "REVISION_CONFLICT"

    def __init__(self, expected_revision: str, current_revision: str) -> None:
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(
            "J source revision changed: "
            f"expected {expected_revision}, current {current_revision}"
        )


class JValidationError(ValueError):
    code = "VALIDATION_ERROR"

    def __init__(
        self,
        message: str,
        *,
        operation_index: int | None = None,
        item_id: str | None = None,
        field: str | None = None,
    ) -> None:
        self.operation_index = operation_index
        self.item_id = item_id
        self.field = field
        super().__init__(message)

    def with_operation(
        self, operation_index: int, item_id: str | None
    ) -> "JValidationError":
        return JValidationError(
            str(self),
            operation_index=operation_index,
            item_id=self.item_id or item_id,
            field=self.field,
        )


def resolve_j_source_file(source_file: str | Path | None = None) -> Path:
    raw = source_file or os.getenv("KMLOG_J_SOURCE_FILE", "") or DEFAULT_J_SOURCE_FILE
    return Path(raw).expanduser().resolve()


def _parse_date(field: str, value: Any, *, required: bool = False) -> str | None:
    if value is None or value == "":
        if required:
            raise JValidationError(f"{field} is required", field=field)
        return None
    if not isinstance(value, str):
        raise JValidationError(f"{field} must be YYYY-MM-DD", field=field)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise JValidationError(
            f"{field} must be YYYY-MM-DD: {value}", field=field
        ) from exc
    if parsed.isoformat() != value:
        raise JValidationError(f"{field} must be YYYY-MM-DD: {value}", field=field)
    return value


def _validate_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise JValidationError("item must be a JSON object", field="item")
    unknown = set(item) - (METADATA_FIELDS | {"title", "body"})
    if unknown:
        field = sorted(unknown)[0]
        raise JValidationError(f"unsupported item field: {field}", field=field)

    result = deepcopy(item)
    item_id = result.get("id")
    if not isinstance(item_id, str) or not re.fullmatch(
        r"J-[A-Za-z0-9._-]+", item_id
    ):
        raise JValidationError(
            "id must match J-[A-Za-z0-9._-]+", field="id"
        )
    for field in ("title", "body", "owner", "area"):
        if not isinstance(result.get(field), str) or not result[field].strip():
            raise JValidationError(f"{field} must be non-empty", field=field)
        result[field] = result[field].strip()

    status = result.get("status")
    if status not in {"active", "archived"}:
        raise JValidationError(
            "status must be active or archived", field="status"
        )

    created_at = _parse_date("created_at", result.get("created_at"), required=True)
    expires_at = _parse_date("expires_at", result.get("expires_at"))
    review_on = _parse_date("review_on", result.get("review_on"))
    archived_at = _parse_date("archived_at", result.get("archived_at"))
    if expires_at is None and review_on is None:
        raise JValidationError(
            "expires_at or review_on is required",
            field="expires_at_or_review_on",
        )
    for field, value in (("expires_at", expires_at), ("review_on", review_on)):
        if value is not None and value < created_at:
            raise JValidationError(
                f"{field} must not be before created_at", field=field
            )

    if status == "active":
        if archived_at is not None or result.get("archive_reason") not in (None, ""):
            field = "archived_at" if archived_at is not None else "archive_reason"
            raise JValidationError(
                "active items cannot contain archive metadata", field=field
            )
    else:
        if archived_at is None:
            raise JValidationError("archived_at is required", field="archived_at")
        reason = result.get("archive_reason")
        if not isinstance(reason, str) or not reason.strip():
            raise JValidationError(
                "archive_reason is required", field="archive_reason"
            )
        result["archive_reason"] = reason.strip()

    for field, value in (
        ("created_at", created_at),
        ("expires_at", expires_at),
        ("review_on", review_on),
        ("archived_at", archived_at),
    ):
        if value is None:
            result.pop(field, None)
        else:
            result[field] = value
    if status == "active":
        result.pop("archive_reason", None)
    return result


def _strip_container_tail(value: str) -> str:
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and (
        CONTAINER_LINE_RE.fullmatch(lines[-1].strip())
        or lines[-1].strip() == "_No items._"
    ):
        lines.pop()
        while lines and not lines[-1].strip():
            lines.pop()
    return "\n".join(lines).strip()


def parse_j_markdown(text: str) -> dict[str, Any]:
    matches = list(ITEM_HEADING_RE.finditer(text))
    if not matches:
        raise ValueError("J source contains no structured items")

    prefix = text[: matches[0].start()]
    prefix_lines = prefix.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while prefix_lines and (
        not prefix_lines[-1].strip()
        or CONTAINER_LINE_RE.fullmatch(prefix_lines[-1].strip())
        or prefix_lines[-1].strip() == "_No items._"
    ):
        prefix_lines.pop()
    preamble = "\n".join(prefix_lines).rstrip()

    items: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment = text[match.end() : end]
        metadata_match = META_RE.match(segment)
        if metadata_match is None:
            raise ValueError(f"J item {match.group('id')} is missing j-item metadata")
        try:
            metadata = json.loads(metadata_match.group("json"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid metadata for J item {match.group('id')}: {exc}") from exc
        if not isinstance(metadata, dict):
            raise ValueError(f"J item {match.group('id')} metadata must be an object")
        if metadata.get("id") != match.group("id"):
            raise ValueError(f"J item heading and metadata id differ: {match.group('id')}")
        unknown_metadata = set(metadata) - METADATA_FIELDS
        if unknown_metadata:
            raise ValueError(
                f"Unsupported J metadata fields for {match.group('id')}: "
                f"{sorted(unknown_metadata)}"
            )
        body = _strip_container_tail(segment[metadata_match.end() :])
        item = _validate_item(
            {**metadata, "title": match.group("title").strip(), "body": body}
        )
        if item["id"] in ids:
            raise ValueError(f"Duplicate J item id: {item['id']}")
        ids.add(item["id"])
        items.append(item)

    return {"preamble": preamble, "items": items}


def _metadata_for_item(item: dict[str, Any]) -> dict[str, Any]:
    return {field: item[field] for field in METADATA_FIELD_ORDER if field in item}


def _render_item(item: dict[str, Any]) -> str:
    metadata = json.dumps(_metadata_for_item(item), ensure_ascii=False, indent=2)
    return (
        f"### [{item['id']}] {item['title']}\n"
        f"<!-- j-item\n{metadata}\n-->\n"
        f"{item['body'].strip()}\n"
    )


def serialize_j_document(document: dict[str, Any], newline: str = "\n") -> str:
    items = [_validate_item(item) for item in document["items"]]
    active = [item for item in items if item["status"] == "active"]
    archived = [item for item in items if item["status"] == "archived"]
    parts = [document["preamble"].rstrip(), "", "## Active", ""]
    if active:
        parts.append("\n\n".join(_render_item(item).rstrip() for item in active))
    else:
        parts.append("_No items._")
    parts.extend(["", "## Archive", ""])
    if archived:
        parts.append("\n\n".join(_render_item(item).rstrip() for item in archived))
    else:
        parts.append("_No items._")
    return ("\n".join(parts).rstrip() + "\n").replace("\n", newline)


def load_j_document(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        raise RuntimeError(f"J source not found: {path}")
    text = read_utf8_text(path)
    return parse_j_markdown(text), text


def _cleanup_candidates(
    items: list[dict[str, Any]], as_of: date | None = None
) -> list[dict[str, str]]:
    today = as_of or datetime.now(timezone.utc).date()
    candidates: list[dict[str, str]] = []
    for item in items:
        if item["status"] != "active":
            continue
        expires_at = item.get("expires_at")
        review_on = item.get("review_on")
        if expires_at and date.fromisoformat(expires_at) <= today:
            candidates.append(
                {"id": item["id"], "reason": "expired", "effective_date": expires_at}
            )
        elif review_on and date.fromisoformat(review_on) <= today:
            candidates.append(
                {"id": item["id"], "reason": "review_due", "effective_date": review_on}
            )
    return candidates


def _source_result(path: Path, document: dict[str, Any], text: str) -> dict[str, Any]:
    items = document["items"]
    active = [deepcopy(item) for item in items if item["status"] == "active"]
    archived = [deepcopy(item) for item in items if item["status"] == "archived"]
    return {
        "source_file": str(path),
        "revision": text_revision(text),
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        "item_count": len(items),
        "active_count": len(active),
        "archived_count": len(archived),
        "active_items": active,
        "archived_items": archived,
        "cleanup_candidates": _cleanup_candidates(items),
    }


def get_j_source_info(source_file: str | Path | None = None) -> dict[str, Any]:
    path = resolve_j_source_file(source_file)
    document, text = load_j_document(path)
    return _source_result(path, document, text)


def _apply_operation(
    items: list[dict[str, Any]], operation: dict[str, Any]
) -> None:
    op = operation.get("op")
    index_by_id = {item["id"]: index for index, item in enumerate(items)}

    if op == "create":
        if set(operation) != {"op", "item"}:
            raise JValidationError(
                "create requires exactly op and item", field="operation"
            )
        item = _validate_item(operation.get("item"))
        if item["status"] != "active":
            raise JValidationError(
                "create only accepts active items", field="status"
            )
        if item["id"] in index_by_id:
            raise JValidationError(
                f"item already exists: {item['id']}",
                item_id=item["id"],
                field="id",
            )
        archive_index = next(
            (
                index
                for index, existing in enumerate(items)
                if existing["status"] == "archived"
            ),
            len(items),
        )
        items.insert(archive_index, item)
        return

    item_id = operation.get("id")
    if not isinstance(item_id, str) or item_id not in index_by_id:
        raise JValidationError(
            f"item not found: {item_id}", item_id=item_id, field="id"
        )
    item = items[index_by_id[item_id]]

    if op == "patch":
        if set(operation) != {"op", "id", "changes"}:
            raise JValidationError(
                "patch requires exactly op, id, and changes", field="operation"
            )
        if item["status"] != "active":
            raise JValidationError(
                "archived items cannot be patched", field="status"
            )
        changes = operation.get("changes")
        if not isinstance(changes, dict) or not changes:
            raise JValidationError(
                "changes must be a non-empty object", field="changes"
            )
        unknown = set(changes) - PATCH_FIELDS
        if unknown:
            field = sorted(unknown)[0]
            if field in METADATA_FIELDS | {"id", "status"}:
                message = f"{field} is immutable"
            else:
                message = f"unsupported patch field: {field}"
            raise JValidationError(message, field=field)
        candidate = {**item, **changes}
        items[index_by_id[item_id]] = _validate_item(candidate)
    elif op == "archive":
        required_fields = {"op", "id", "reason", "archived_at"}
        if set(operation) != required_fields:
            missing = required_fields - set(operation)
            field = sorted(missing)[0] if missing else "operation"
            raise JValidationError(
                "archive requires exactly op, id, reason, and archived_at",
                field=field,
            )
        if item["status"] != "active":
            raise JValidationError("item is already archived", field="status")
        candidate = {
            **item,
            "status": "archived",
            "archive_reason": operation.get("reason"),
            "archived_at": operation.get("archived_at"),
        }
        items.pop(index_by_id[item_id])
        items.append(_validate_item(candidate))
    else:
        raise JValidationError(f"unsupported operation: {op}", field="op")


def _apply_operations(
    document: dict[str, Any], operations: list[dict[str, Any]]
) -> dict[str, Any]:
    if not operations:
        raise JValidationError("operations must not be empty", field="operations")
    if len(operations) > 100:
        raise JValidationError(
            "operations must contain at most 100 items", field="operations"
        )

    result = deepcopy(document)
    items = result["items"]
    for operation_index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise JValidationError(
                "operation must be a JSON object",
                operation_index=operation_index,
                field="operation",
            )
        item_id = operation.get("id")
        if item_id is None and isinstance(operation.get("item"), dict):
            item_id = operation["item"].get("id")
        try:
            _apply_operation(items, operation)
        except JValidationError as exc:
            raise exc.with_operation(operation_index, item_id) from exc
        except ValueError as exc:
            raise JValidationError(
                str(exc), operation_index=operation_index, item_id=item_id
            ) from exc
    return result


def _changed_item_ids(
    before: dict[str, Any], after: dict[str, Any]
) -> list[str]:
    before_by_id = {item["id"]: item for item in before["items"]}
    after_by_id = {item["id"]: item for item in after["items"]}
    ordered_ids = [item["id"] for item in after["items"]]
    return [
        item_id
        for item_id in ordered_ids
        if before_by_id.get(item_id) != after_by_id.get(item_id)
    ]


def preview_j_update(
    expected_revision: str,
    operations: list[dict[str, Any]],
    source_file: str | Path | None = None,
) -> dict[str, Any]:
    path = resolve_j_source_file(source_file)
    if not path.exists():
        raise RuntimeError(f"J source not found: {path}")
    text = read_utf8_text(path)
    current_revision = text_revision(text)
    if expected_revision != current_revision:
        raise JRevisionConflictError(expected_revision, current_revision)
    document = parse_j_markdown(text)
    result = _apply_operations(document, operations)
    changed_item_ids = _changed_item_ids(document, result)
    noop = not changed_item_ids
    newline = "\r\n" if "\r\n" in text else "\n"
    result_text = text if noop else serialize_j_document(result, newline)
    return {
        "valid": True,
        "noop": noop,
        "before_revision": current_revision,
        "after_revision": text_revision(result_text),
        "changed_item_ids": changed_item_ids,
        "cleanup_candidates": _cleanup_candidates(result["items"]),
        "diff": "" if noop else unified_text_diff(path, text, result_text),
    }


def apply_j_update(
    expected_revision: str,
    operations: list[dict[str, Any]],
    actor: str | None = None,
    source_file: str | Path | None = None,
) -> dict[str, Any]:
    path = resolve_j_source_file(source_file)
    with J_WRITE_LOCK:
        if not path.exists():
            raise RuntimeError(f"J source not found: {path}")
        text = read_utf8_text(path)
        current_revision = text_revision(text)
        if expected_revision != current_revision:
            raise JRevisionConflictError(expected_revision, current_revision)
        document = parse_j_markdown(text)
        result = _apply_operations(document, operations)
        changed_item_ids = _changed_item_ids(document, result)
        if not changed_item_ids:
            return {
                "applied": False,
                "noop": True,
                "actor": actor,
                "before_revision": current_revision,
                "after_revision": current_revision,
                "changed_item_ids": [],
                "cleanup_candidates": _cleanup_candidates(document["items"]),
                "backup_file": None,
            }

        newline = "\r\n" if "\r\n" in text else "\n"
        result_text = serialize_j_document(result, newline)
        after_revision = text_revision(result_text)
        backup_path = atomic_replace_with_backup(path, result_text)

        readback_document, readback_text = load_j_document(path)
        readback_revision = text_revision(readback_text)
        if readback_revision != after_revision or readback_document != result:
            raise RuntimeError("J source readback verification failed")
        return {
            "applied": True,
            "noop": False,
            "actor": actor,
            "before_revision": current_revision,
            "after_revision": after_revision,
            "changed_item_ids": changed_item_ids,
            "cleanup_candidates": _cleanup_candidates(result["items"]),
            "backup_file": str(backup_path),
            "readback_verified": True,
        }
