"""Opt-in body-evidence search, keeping the legacy tuple API unchanged."""

import hashlib
import math
import re


def _terms(query, evidence_terms):
    terms = []
    for value in list(evidence_terms or []) + query.split():
        value = str(value).strip()
        valid_length = 2 <= len(value) <= 160 or (
            len(value) == 1 and not value.isascii() and value.isalnum()
        )
        if valid_length and value.casefold() not in {t.casefold() for t in terms}:
            terms.append(value)
        if len(terms) == 10:
            break
    return terms


def _matches(text, term):
    # Latin entities must not match a different entity, e.g. ISTA in FISTA.
    pattern = re.escape(term)
    if term and term[0].isascii() and term[0].isalnum():
        pattern = r"(?<![a-zA-Z0-9_])" + pattern
    if term and term[-1].isascii() and term[-1].isalnum():
        pattern += r"(?![a-zA-Z0-9_])"
    return list(re.finditer(pattern, text, re.IGNORECASE))


def _excerpts(content, spans):
    # Terms arrive in planner priority order. Select before sorting by offset,
    # so optional early mentions cannot displace the required entity window.
    windows = []
    for priority, span in enumerate(spans[:3]):
        start = max(0, span["start"] - 60)
        end = min(len(content), max(start + 240, span["end"]))
        windows.append({"start": start, "end": end, "priority": priority})
    merged = []
    for window in sorted(windows, key=lambda w: w["start"]):
        if merged and window["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], window["end"])
            merged[-1]["priority"] = min(merged[-1]["priority"], window["priority"])
        else:
            merged.append(dict(window))
    return [
        {"start": w["start"], "end": w["end"], "text": content[w["start"]:w["end"]]}
        for w in sorted(merged, key=lambda w: w["priority"])
    ]


def search_with_evidence(query, *, legacy_search, connection_factory, limit=10,
                         mode="auto", kinds=None, after=None, before=None,
                         evidence_terms=None, candidate_limit=None):
    limit = max(1, min(int(limit), 50))
    if candidate_limit is None:
        candidate_limit = max(40, limit * 4)
    candidate_limit = max(limit, min(int(candidate_limit), 200))
    terms = _terms(query, evidence_terms)
    if not query.strip():
        return {
            "results": [],
            "evidence_version": 1,
            "candidate_limit": candidate_limit,
            "candidate_ids": [],
            "selected_ids": [],
            "candidate_count": 0,
            "selected_count": 0,
            "deduplicated_count": 0,
        }
    legacy_rows, _ = legacy_search(query, limit=candidate_limit, mode=mode,
                                  kinds=kinds, after=after, before=before)
    legacy = {row[0]: row for row in legacy_rows}
    kinds = [kind for kind in (kinds or ["chat"]) if kind] or ["chat"]
    where = "kind IN (" + ",".join("?" for _ in kinds) + ")"
    where += " AND (? IS NULL OR timestamp >= ?) AND (? IS NULL OR timestamp <= ?)"
    filters = [*kinds, after, after, before, before]
    columns = "id, timestamp, role, content, conversation_title"
    conn = connection_factory()
    pool = {}
    try:
        if legacy:
            placeholders = ",".join("?" for _ in legacy)
            for row in conn.execute(f"SELECT {columns} FROM messages WHERE id IN ({placeholders})", list(legacy)):
                pool[row[0]] = tuple(row)
        # Independent per-term body pools prevent titles from consuming all slots.
        per_term_limit = max(limit, math.ceil(candidate_limit / max(1, len(terms))))
        for term in terms:
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            sql = (f"SELECT {columns} FROM messages WHERE {where} "
                   "AND content LIKE ? ESCAPE '\\' ORDER BY timestamp DESC, id DESC LIMIT ?")
            for row in conn.execute(sql, [*filters, f"%{escaped}%", per_term_limit]):
                pool[row[0]] = tuple(row)
    finally:
        conn.close()
    results = []
    for row in pool.values():
        message_id, timestamp, role, content, title = row
        content, title = content or "", title or ""
        spans = []
        body_terms, title_terms = [], []
        for term in terms:
            hits = _matches(content, term)
            if hits:
                body_terms.append(term)
                hit = hits[0]
                spans.append({"term": term, "start": hit.start(), "end": hit.end()})
            if _matches(title, term):
                title_terms.append(term)
        # Excerpt is evidence, not a prefix that may omit the matching entity.
        excerpts = _excerpts(content, spans)
        old = legacy.get(message_id)
        evidence_origin = "body" if body_terms else ("title" if title_terms else "legacy")
        results.append({
            "id": message_id, "timestamp": str(timestamp), "role": role,
            "conversation_title": title, "content_preview": content[:160],
            "matched_excerpt": "\n...\n".join(e["text"] for e in excerpts),
            "body_matched_terms": body_terms, "title_matched_terms": title_terms,
            "evidence_origin": evidence_origin,
            "title_only": evidence_origin == "title",
            "match_spans": spans, "evidence_excerpts": excerpts,
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "content_length": len(content),
            "synthetic_context": content.lstrip().startswith("The following context is provided by the system."),
            "relevance": float(old[5] or 0) if old else 0.0,
            "match_type": "content" if body_terms else (old[6] if old else "title"),
            "token_hits": len(body_terms), "evidence_version": 1,
        })
    results.sort(
        key=lambda r: (
            bool(r["body_matched_terms"]),
            len(r["body_matched_terms"]),
            r["relevance"],
            r["timestamp"],
            r["id"],
        ),
        reverse=True,
    )
    candidates = results[:candidate_limit]

    # Collapse only byte-identical bodies. The representative keeps every source
    # message as provenance so callers can inspect distinct dates/conversations.
    grouped = []
    by_hash = {}
    for result in candidates:
        provenance = {
            "id": result["id"],
            "timestamp": result["timestamp"],
            "role": result["role"],
            "conversation_title": result["conversation_title"],
        }
        group = by_hash.get(result["content_hash"]) if result["content_length"] else None
        if group is None:
            result["source_message_ids"] = [result["id"]]
            result["duplicate_count"] = 1
            result["duplicate_provenance"] = [provenance]
            if result["content_length"]:
                by_hash[result["content_hash"]] = result
            grouped.append(result)
        else:
            group["source_message_ids"].append(result["id"])
            group["duplicate_count"] += 1
            group["duplicate_provenance"].append(provenance)

    selected = grouped[:limit]
    return {
        "results": selected,
        "evidence_version": 1,
        "candidate_limit": candidate_limit,
        "candidate_ids": [r["id"] for r in candidates],
        "selected_ids": [r["id"] for r in selected],
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "deduplicated_count": len(candidates) - len(grouped),
    }
