import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from typing import Callable


MAX_BATCH_OPERATIONS = 500
ALLOWED_TRANSITIONS = {
    "candidate": {"accepted", "deferred", "merged", "rejected"},
    "deferred": {"accepted", "merged", "rejected"},
}
MERGE_TARGET_TYPES = {
    "reviewed_item",
    "mother_section",
    "worldbook_entry",
    "j_item",
    "canonical_topic",
}
REJECTION_REASON_CODES = {
    "completed_one_off",
    "expired_daily_context",
    "transient_health_snapshot",
    "assistant_only_interpretation",
    "insufficient_specificity",
    "stale_infra_incident",
    "decorative_daily_slice",
    "non_durable_duplicate",
}
SENSITIVE_DOMAINS = {"health_safety", "rule", "milestone"}
SENSITIVE_FUNCTIONS = {"boot_core", "soothe_panic"}
OPERATION_FIELDS = {
    "candidate_id",
    "status",
    "reason_code",
    "merge_target",
    "review_note",
    "manual_override",
}


class CandidateReviewBatchError(ValueError):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.details = details

    def as_detail(self) -> dict:
        return {"code": self.code, "message": str(self), **self.details}


def ensure_candidate_review_schema(cursor) -> None:
    columns = {
        row[1]
        for row in cursor.execute("PRAGMA table_info(daily_memory_candidates)").fetchall()
    }
    if "updated_at" not in columns:
        cursor.execute("ALTER TABLE daily_memory_candidates ADD COLUMN updated_at TEXT")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_review_batches (
          batch_id TEXT PRIMARY KEY,
          actor TEXT NOT NULL,
          scope_json TEXT NOT NULL,
          preview_digest TEXT NOT NULL,
          before_counts_json TEXT NOT NULL,
          after_counts_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          applied_at TEXT NOT NULL,
          status TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_review_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          batch_id TEXT NOT NULL,
          candidate_id INTEGER NOT NULL,
          old_status TEXT NOT NULL,
          new_status TEXT NOT NULL,
          reason_code TEXT,
          merge_target_type TEXT,
          merge_target_ref TEXT,
          review_note TEXT,
          manual_override INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          UNIQUE(batch_id, candidate_id),
          FOREIGN KEY(batch_id) REFERENCES candidate_review_batches(batch_id),
          FOREIGN KEY(candidate_id) REFERENCES daily_memory_candidates(id)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidate_review_events_candidate "
        "ON candidate_review_events(candidate_id)"
    )


def _clean_text(value, field: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise CandidateReviewBatchError(
                "VALIDATION_ERROR", f"{field} is required", field=field
            )
        return None
    if not isinstance(value, str):
        raise CandidateReviewBatchError(
            "VALIDATION_ERROR", f"{field} must be a string", field=field
        )
    value = value.strip()
    if required and not value:
        raise CandidateReviewBatchError(
            "VALIDATION_ERROR", f"{field} must not be empty", field=field
        )
    return value or None


def _normalize_manifest(batch_id, actor, scope, operations) -> dict:
    batch_id = _clean_text(batch_id, "batch_id", required=True)
    actor = _clean_text(actor, "actor", required=True)
    if not isinstance(scope, dict):
        raise CandidateReviewBatchError(
            "VALIDATION_ERROR", "scope must be an object", field="scope"
        )
    allowed_scope_fields = {
        "start_date",
        "end_date",
        "required_current_status",
        "expected_count",
    }
    unknown_scope = sorted(set(scope) - allowed_scope_fields)
    if unknown_scope:
        raise CandidateReviewBatchError(
            "VALIDATION_ERROR",
            f"Unsupported scope fields: {unknown_scope}",
            field="scope",
        )
    start_date = _clean_text(scope.get("start_date"), "scope.start_date", required=True)
    end_date = _clean_text(scope.get("end_date"), "scope.end_date", required=True)
    try:
        start_value = date.fromisoformat(start_date)
        end_value = date.fromisoformat(end_date)
    except ValueError as exc:
        raise CandidateReviewBatchError(
            "VALIDATION_ERROR",
            "scope dates must use YYYY-MM-DD",
            field="scope",
        ) from exc
    if start_value > end_value:
        raise CandidateReviewBatchError(
            "VALIDATION_ERROR",
            "scope.start_date must not be after scope.end_date",
            field="scope",
        )
    required_status = _clean_text(
        scope.get("required_current_status"),
        "scope.required_current_status",
        required=True,
    )
    if required_status not in ALLOWED_TRANSITIONS:
        raise CandidateReviewBatchError(
            "VALIDATION_ERROR",
            "scope.required_current_status must be one of "
            f"{sorted(ALLOWED_TRANSITIONS)}",
            field="scope.required_current_status",
        )
    expected_count = scope.get("expected_count")
    if isinstance(expected_count, bool) or not isinstance(expected_count, int):
        raise CandidateReviewBatchError(
            "VALIDATION_ERROR",
            "scope.expected_count must be an integer",
            field="scope.expected_count",
        )
    if expected_count < 1 or expected_count > MAX_BATCH_OPERATIONS:
        raise CandidateReviewBatchError(
            "VALIDATION_ERROR",
            f"scope.expected_count must be between 1 and {MAX_BATCH_OPERATIONS}",
            field="scope.expected_count",
        )
    if not isinstance(operations, list) or not operations:
        raise CandidateReviewBatchError(
            "VALIDATION_ERROR", "operations must not be empty", field="operations"
        )
    if len(operations) > MAX_BATCH_OPERATIONS:
        raise CandidateReviewBatchError(
            "VALIDATION_ERROR",
            f"operations must contain at most {MAX_BATCH_OPERATIONS} items",
            field="operations",
        )

    normalized_operations = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise CandidateReviewBatchError(
                "VALIDATION_ERROR",
                "operation must be an object",
                operation_index=index,
            )
        unknown = sorted(set(operation) - OPERATION_FIELDS)
        if unknown:
            raise CandidateReviewBatchError(
                "VALIDATION_ERROR",
                f"Unsupported operation fields: {unknown}",
                operation_index=index,
            )
        candidate_id = operation.get("candidate_id")
        if isinstance(candidate_id, bool) or not isinstance(candidate_id, int) or candidate_id < 1:
            raise CandidateReviewBatchError(
                "VALIDATION_ERROR",
                "candidate_id must be a positive integer",
                operation_index=index,
                field="candidate_id",
            )
        status = _clean_text(operation.get("status"), "status")
        reason_code = _clean_text(operation.get("reason_code"), "reason_code")
        review_note = _clean_text(operation.get("review_note"), "review_note")
        manual_override = operation.get("manual_override", False)
        if not isinstance(manual_override, bool):
            raise CandidateReviewBatchError(
                "VALIDATION_ERROR",
                "manual_override must be a boolean",
                operation_index=index,
                candidate_id=candidate_id,
                field="manual_override",
            )
        merge_target = operation.get("merge_target")
        if merge_target is not None:
            if not isinstance(merge_target, dict):
                raise CandidateReviewBatchError(
                    "VALIDATION_ERROR",
                    "merge_target must be an object",
                    operation_index=index,
                    candidate_id=candidate_id,
                    field="merge_target",
                )
            unknown_target = sorted(set(merge_target) - {"type", "ref"})
            if unknown_target:
                raise CandidateReviewBatchError(
                    "VALIDATION_ERROR",
                    f"Unsupported merge_target fields: {unknown_target}",
                    operation_index=index,
                    candidate_id=candidate_id,
                    field="merge_target",
                )
            merge_target = {
                "type": _clean_text(merge_target.get("type"), "merge_target.type"),
                "ref": _clean_text(merge_target.get("ref"), "merge_target.ref"),
            }
        normalized_operations.append(
            {
                "_operation_index": index,
                "candidate_id": candidate_id,
                "status": status,
                "reason_code": reason_code,
                "merge_target": merge_target,
                "review_note": review_note,
                "manual_override": manual_override,
            }
        )

    normalized_operations.sort(key=lambda item: item["candidate_id"])
    return {
        "batch_id": batch_id,
        "actor": actor,
        "scope": {
            "start_date": start_date,
            "end_date": end_date,
            "required_current_status": required_status,
            "expected_count": expected_count,
        },
        "operations": normalized_operations,
    }


def _manifest_digest(manifest: dict) -> str:
    digest_manifest = {
        **manifest,
        "operations": [
            {key: value for key, value in operation.items() if key != "_operation_index"}
            for operation in manifest["operations"]
        ],
    }
    encoded = json.dumps(
        digest_manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _json_load_object(value: str) -> dict:
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


def _applied_noop(batch_row, manifest: dict, digest: str) -> dict:
    if batch_row["preview_digest"] != digest:
        raise CandidateReviewBatchError(
            "BATCH_ID_CONFLICT",
            "batch_id was already applied with a different manifest",
            batch_id=manifest["batch_id"],
            current_preview_digest=batch_row["preview_digest"],
            requested_preview_digest=digest,
        )
    before_counts = _json_load_object(batch_row["before_counts_json"])
    after_counts = _json_load_object(batch_row["after_counts_json"])
    candidate_ids = defaultdict(list)
    for operation in manifest["operations"]:
        candidate_ids[operation["status"]].append(operation["candidate_id"])
    return {
        "valid": True,
        "noop": True,
        "batch_id": manifest["batch_id"],
        "preview_digest": digest,
        "scope": {
            "matched_count": sum(before_counts.values()),
            "expected_count": manifest["scope"]["expected_count"],
        },
        "before_counts": before_counts,
        "proposed_counts": after_counts,
        "candidate_ids": dict(candidate_ids),
        "warnings": ["This batch was already applied with the same manifest."],
        "conflicts": [],
        "invalid_operations": [],
        "applied_at": batch_row["applied_at"],
    }


def _evaluate(cursor, manifest: dict, digest: str) -> dict:
    batch_row = cursor.execute(
        "SELECT * FROM candidate_review_batches WHERE batch_id = ?",
        (manifest["batch_id"],),
    ).fetchone()
    if batch_row is not None:
        return _applied_noop(batch_row, manifest, digest)

    scope = manifest["scope"]
    scope_rows = cursor.execute(
        """
        SELECT * FROM daily_memory_candidates
        WHERE date_key >= ? AND date_key <= ? AND status = ?
        ORDER BY id ASC
        """,
        (scope["start_date"], scope["end_date"], scope["required_current_status"]),
    ).fetchall()
    scoped_by_id = {row["id"]: row for row in scope_rows}
    operation_ids = [operation["candidate_id"] for operation in manifest["operations"]]
    duplicate_ids = sorted(
        candidate_id
        for candidate_id, count in Counter(operation_ids).items()
        if count > 1
    )
    conflicts = []
    if len(scope_rows) != scope["expected_count"]:
        conflicts.append(
            {
                "code": "SCOPE_COUNT_MISMATCH",
                "expected_count": scope["expected_count"],
                "matched_count": len(scope_rows),
            }
        )
    if duplicate_ids:
        conflicts.append({"code": "DUPLICATE_CANDIDATE_IDS", "candidate_ids": duplicate_ids})
    requested_set = set(operation_ids)
    scoped_set = set(scoped_by_id)
    missing_from_manifest = sorted(scoped_set - requested_set)
    if missing_from_manifest:
        conflicts.append(
            {"code": "SCOPE_CANDIDATES_MISSING_FROM_MANIFEST", "candidate_ids": missing_from_manifest}
        )

    out_of_scope_ids = sorted(requested_set - scoped_set)
    if out_of_scope_ids:
        placeholders = ",".join("?" for _ in out_of_scope_ids)
        found = cursor.execute(
            f"SELECT id, date_key, status FROM daily_memory_candidates WHERE id IN ({placeholders})",
            out_of_scope_ids,
        ).fetchall()
        found_by_id = {row["id"]: row for row in found}
        for candidate_id in out_of_scope_ids:
            row = found_by_id.get(candidate_id)
            conflicts.append(
                {
                    "code": "CANDIDATE_NOT_FOUND" if row is None else "CANDIDATE_OUT_OF_SCOPE",
                    "candidate_id": candidate_id,
                    **(
                        {}
                        if row is None
                        else {"current_status": row["status"], "date_key": row["date_key"]}
                    ),
                }
            )

    invalid_operations = []
    candidate_ids = defaultdict(list)
    proposed_counts = Counter()
    allowed_statuses = ALLOWED_TRANSITIONS[scope["required_current_status"]]
    for operation in manifest["operations"]:
        candidate_id = operation["candidate_id"]
        status = operation["status"]
        row = scoped_by_id.get(candidate_id)
        errors = []
        if status not in allowed_statuses:
            errors.append(
                {
                    "field": "status",
                    "message": (
                        f"status transition from {scope['required_current_status']} "
                        f"must end in one of {sorted(allowed_statuses)}"
                    ),
                }
            )
        if status == "merged":
            target = operation["merge_target"]
            if not target or not target.get("type") or not target.get("ref"):
                errors.append(
                    {"field": "merge_target", "message": "merged requires a non-empty merge_target"}
                )
            elif target["type"] not in MERGE_TARGET_TYPES:
                errors.append(
                    {
                        "field": "merge_target.type",
                        "message": f"merge_target.type must be one of {sorted(MERGE_TARGET_TYPES)}",
                    }
                )
            if not operation["reason_code"]:
                errors.append({"field": "reason_code", "message": "merged requires reason_code"})
        elif operation["merge_target"] is not None:
            errors.append(
                {"field": "merge_target", "message": "merge_target is only valid for merged"}
            )
        if status == "rejected":
            if operation["reason_code"] not in REJECTION_REASON_CODES:
                errors.append(
                    {
                        "field": "reason_code",
                        "message": f"rejected reason_code must be one of {sorted(REJECTION_REASON_CODES)}",
                    }
                )
            if row is not None:
                sensitive = (
                    (row["importance"] is not None and row["importance"] >= 4)
                    or row["domain"] in SENSITIVE_DOMAINS
                    or row["function"] in SENSITIVE_FUNCTIONS
                )
                if sensitive and not operation["manual_override"]:
                    errors.append(
                        {"field": "manual_override", "message": "sensitive rejection requires manual_override=true"}
                    )
                if sensitive and not operation["review_note"]:
                    errors.append(
                        {"field": "review_note", "message": "sensitive rejection requires review_note"}
                    )
        if errors:
            invalid_operations.append(
                {
                    "operation_index": operation["_operation_index"],
                    "candidate_id": candidate_id,
                    "errors": errors,
                }
            )
        if status in allowed_statuses:
            candidate_ids[status].append(candidate_id)
            proposed_counts[status] += 1

    return {
        "valid": not conflicts and not invalid_operations,
        "noop": False,
        "batch_id": manifest["batch_id"],
        "preview_digest": digest,
        "scope": {
            "matched_count": len(scope_rows),
            "expected_count": scope["expected_count"],
        },
        "before_counts": dict(Counter(row["status"] for row in scope_rows)),
        "proposed_counts": dict(proposed_counts),
        "candidate_ids": dict(candidate_ids),
        "warnings": [],
        "conflicts": conflicts,
        "invalid_operations": invalid_operations,
    }


def preview_memory_candidate_review_batch(
    connection_factory: Callable,
    *,
    batch_id: str,
    actor: str,
    scope: dict,
    operations: list[dict],
) -> dict:
    manifest = _normalize_manifest(batch_id, actor, scope, operations)
    digest = _manifest_digest(manifest)
    conn = connection_factory()
    try:
        return _evaluate(conn.cursor(), manifest, digest)
    finally:
        conn.close()


def apply_memory_candidate_review_batch(
    connection_factory: Callable,
    *,
    preview_digest: str,
    batch_id: str,
    actor: str,
    scope: dict,
    operations: list[dict],
) -> dict:
    manifest = _normalize_manifest(batch_id, actor, scope, operations)
    digest = _manifest_digest(manifest)
    preview_digest = _clean_text(preview_digest, "preview_digest", required=True)
    if preview_digest != digest:
        raise CandidateReviewBatchError(
            "PREVIEW_DIGEST_MISMATCH",
            "Apply manifest does not match the preview digest",
            expected_preview_digest=digest,
            provided_preview_digest=preview_digest,
        )

    conn = connection_factory()
    try:
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        preview = _evaluate(cursor, manifest, digest)
        if preview["noop"]:
            conn.commit()
            return {
                "applied": False,
                "noop": True,
                "batch_id": manifest["batch_id"],
                "preview_digest": digest,
                "changed_count": 0,
                "before_counts": preview["before_counts"],
                "after_counts": preview["proposed_counts"],
                "conflicts": [],
                "readback_verified": True,
                "applied_at": preview["applied_at"],
            }
        if not preview["valid"]:
            raise CandidateReviewBatchError(
                "BATCH_VALIDATION_FAILED",
                "Candidate review batch is no longer valid",
                conflicts=preview["conflicts"],
                invalid_operations=preview["invalid_operations"],
            )

        ids = [operation["candidate_id"] for operation in manifest["operations"]]
        placeholders = ",".join("?" for _ in ids)
        before_rows = cursor.execute(
            f"SELECT * FROM daily_memory_candidates WHERE id IN ({placeholders}) ORDER BY id",
            ids,
        ).fetchall()
        before_by_id = {row["id"]: dict(row) for row in before_rows}
        now = datetime.now(timezone.utc).isoformat()
        for operation in manifest["operations"]:
            cursor.execute(
                """
                UPDATE daily_memory_candidates
                SET status = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    operation["status"],
                    now,
                    operation["candidate_id"],
                    manifest["scope"]["required_current_status"],
                ),
            )
            if cursor.rowcount != 1:
                raise CandidateReviewBatchError(
                    "ROW_CONFLICT",
                    "Candidate changed while the batch was being applied",
                    candidate_id=operation["candidate_id"],
                )

        after_counts = dict(Counter(operation["status"] for operation in manifest["operations"]))
        cursor.execute(
            """
            INSERT INTO candidate_review_batches (
                batch_id, actor, scope_json, preview_digest,
                before_counts_json, after_counts_json,
                created_at, applied_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'applied')
            """,
            (
                manifest["batch_id"],
                manifest["actor"],
                json.dumps(manifest["scope"], ensure_ascii=False, sort_keys=True),
                digest,
                json.dumps(preview["before_counts"], sort_keys=True),
                json.dumps(after_counts, sort_keys=True),
                now,
                now,
            ),
        )
        for operation in manifest["operations"]:
            target = operation["merge_target"] or {}
            cursor.execute(
                """
                INSERT INTO candidate_review_events (
                    batch_id, candidate_id, old_status, new_status,
                    reason_code, merge_target_type, merge_target_ref,
                    review_note, manual_override, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    manifest["batch_id"],
                    operation["candidate_id"],
                    before_by_id[operation["candidate_id"]]["status"],
                    operation["status"],
                    operation["reason_code"],
                    target.get("type"),
                    target.get("ref"),
                    operation["review_note"],
                    int(operation["manual_override"]),
                    now,
                ),
            )

        after_rows = cursor.execute(
            f"SELECT * FROM daily_memory_candidates WHERE id IN ({placeholders}) ORDER BY id",
            ids,
        ).fetchall()
        after_by_id = {row["id"]: dict(row) for row in after_rows}
        expected_statuses = {
            operation["candidate_id"]: operation["status"] for operation in manifest["operations"]
        }
        protected_fields = set(next(iter(before_by_id.values()))) - {"status", "updated_at"}
        readback_verified = len(after_by_id) == len(before_by_id)
        for candidate_id, before in before_by_id.items():
            after = after_by_id.get(candidate_id)
            if after is None or after["status"] != expected_statuses[candidate_id]:
                readback_verified = False
                break
            if any(before[field] != after[field] for field in protected_fields):
                readback_verified = False
                break
        if not readback_verified:
            raise CandidateReviewBatchError(
                "READBACK_FAILED", "Candidate batch readback verification failed"
            )

        conn.commit()
        return {
            "applied": True,
            "noop": False,
            "batch_id": manifest["batch_id"],
            "preview_digest": digest,
            "changed_count": len(ids),
            "before_counts": preview["before_counts"],
            "after_counts": after_counts,
            "conflicts": [],
            "readback_verified": True,
            "applied_at": now,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
