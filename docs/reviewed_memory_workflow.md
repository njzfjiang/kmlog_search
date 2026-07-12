# Reviewed Memory Promotion Workflow

This runbook covers the reviewed-memory layer in `kmlog-search`: how daily
memory candidates move from machine-generated proposals into curated,
queryable `reviewed_memory_items`.

## Scope

Ownership split:

```text
chat-proxy
  generates daily_summaries and daily_memory_candidates

kmlog-search
  lists, dedupes, reviews, promotes, and retrieves memory candidates
```

Do not inject raw `daily_memory_candidates` directly into context. Candidates
are proposals. `reviewed_memory_items` is the curated layer intended for later
retrieval and possible context injection.

## Tables

Candidate proposal table:

```sql
daily_memory_candidates(
  id,
  date_key,
  summary_version,
  label,
  evidence,
  domain,
  function,
  primary_mother,
  secondary_mother,
  importance,
  confidence,
  target_layer,
  source_message_ids_json,
  status,
  metadata_json,
  created_at
)
```

Curated item table:

```sql
reviewed_memory_items(
  id,
  title,
  content,
  evidence,
  domain,
  function,
  primary_mother,
  secondary_mother,
  importance,
  confidence,
  explicitness,
  status,
  source_candidate_ids_json,
  source_message_ids_json,
  reviewer,
  reviewed_at,
  created_at,
  updated_at,
  expires_at,
  superseded_by_item_id,
  metadata_json
)
```

Provenance table:

```sql
reviewed_memory_sources(
  id,
  memory_item_id,
  candidate_id,
  message_pk,
  message_id,
  evidence,
  source_role,
  created_at
)
```

`reviewed_memory_sources` exists so callers can reverse lookup reviewed memory
from source messages without scanning JSON arrays.

## Status model

`daily_memory_candidates.status`:

- `candidate`: machine-generated proposal, not reviewed yet.
- `accepted`: reviewed as useful, but not materialized.
- `rejected`: reviewed as not useful.
- `deferred`: postponed for later review.
- `merged`: folded into another candidate or reviewed item.
- `superseded`: replaced by a later candidate or decision.
- `promoted`: materialized into `reviewed_memory_items`.

`reviewed_memory_items.status`:

- `active`: available for retrieval.
- `archived`: retained for audit, excluded from normal retrieval.
- `superseded`: replaced by another reviewed item.

Expired items are excluded from normal lookup unless `include_expired=true`.

## Weekly review flow

1. Generate daily summaries and candidates in `chat-proxy`.
2. Read weekly candidates from `kmlog-search`.
3. Mark obvious noise as `rejected`.
4. Mark useful-but-not-ready candidates as `accepted` or `deferred`.
5. Merge duplicates conceptually during review.
6. Promote only edited, high-signal candidates.
7. Use reviewed items for retrieval experiments.
8. Manually copy only durable items into mother/WB if they belong in the
   long-term stable layer.

The local review UI is:

```text
GET /memory_week
```

UI actions map to database writes:

- `Accept`: set all candidate IDs in the displayed group to `accepted`.
- `Reject`: set all candidate IDs in the displayed group to `rejected`.
- `Defer`: set all candidate IDs in the displayed group to `deferred`.
- `Reset`: set all candidate IDs in the displayed group back to `candidate`.
- `Promote`: open an edit form and submit to `/memory_candidates/promote`.

Recommended boundary:

```text
daily_memory_candidates = proposal layer
reviewed_memory_items   = curated short/mid-term layer
mother / WB             = durable long-term layer
```

## HTTP endpoints

List daily candidates:

```text
GET /daily_memory_candidates?date_key=2026-05-18&status=candidate&limit=50
```

List weekly deduped candidate groups:

```text
GET /weekly_memory_candidates?start_date=2026-05-18&end_date=2026-05-24&status=candidate&limit=100
```

Update candidate review status:

```text
POST /memory_candidates/83/status
```

```json
{
  "status": "accepted"
}
```

Promote candidates:

```text
POST /memory_candidates/promote
```

```json
{
  "candidate_ids": [83],
  "title": "Curated title",
  "content": "Human-edited memory text suitable for retrieval.",
  "evidence": "Brief factual basis for audit.",
  "domain": "infra",
  "function": "daily_context",
  "primary_mother": "D",
  "secondary_mother": "D.3",
  "importance": 2,
  "confidence": "medium",
  "explicitness": "edited_by_human",
  "reviewer": "human",
  "metadata_json": {
    "review_note": "Edited during weekly review"
  }
}
```

Query reviewed items:

```text
GET /reviewed_memory_items?status=active&function=daily_context&limit=20
GET /reviewed_memory_items?q=keyword&include_sources=true&limit=20
```

Reverse lookup from source message:

```text
GET /reviewed_memory/by_message?message_pk=32149
GET /reviewed_memory/by_message?message_id=<external-message-uuid>
```

`message_pk` means `messages.id`. `message_id` means the external UUID stored
in `messages.message_id`.

## MCP tools

The MCP wrappers expose:

- `get_daily_memory_candidates`
- `get_weekly_memory_candidates`
- `update_memory_candidate_status`
- `promote_memory_candidate`
- `get_reviewed_memory_items`
- `get_reviewed_memory_by_message`

Typical MCP flow:

```text
get_weekly_memory_candidates
  -> update_memory_candidate_status for rejected/deferred/accepted items
  -> promote_memory_candidate for edited curated items
  -> get_reviewed_memory_items to verify retrieval
  -> get_reviewed_memory_by_message to audit provenance
```

## Rewrite guidelines before promotion

Before promoting, rewrite candidate text so it is:

- concise;
- factual;
- stable enough for short/mid-term retrieval;
- free of credentials or private implementation secrets;
- traceable through `evidence` and source message IDs;
- not over-claiming inferred meaning.

Use `explicitness` to indicate how the memory was derived:

- `explicit_user_said`: directly stated by the user.
- `inferred`: inferred from context.
- `assistant_interpreted`: summarized or interpreted by the assistant.
- `edited_by_human`: manually curated wording.

## Retrieval contract

Future context injection should read from `reviewed_memory_items`, not from raw
candidates.

Baseline filter:

```sql
status = 'active'
AND (expires_at IS NULL OR expires_at = '' OR expires_at > current_time)
```

Suggested ranking:

```text
function/domain match
importance DESC
confidence
updated_at DESC
id ASC
```

Start with preview-only retrieval in `chat-proxy` context debugging before
enabling actual injection.

## Maintenance

Archive stale reviewed items instead of deleting them when provenance matters.

Use `superseded_by_item_id` when a newer reviewed item replaces an older one.
Then mark the old item as `superseded`.

Keep mother/WB updates manual. Promote does not write to mother markdown or
world book files.
