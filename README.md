# KMLog Search

Self-hosted chat log search using SQLite + FTS5, with a FastAPI HTTP API and FastMCP wrappers.

## What Is Reusable Here

- `chat_data/clean.py`: extracts the older `chats.json` export format into `cleaned_chats.jsonl`.
- `chat_data/import_chats.py`: extracts the newer `conversations.json` export format into `chatgpt_incremental.jsonl`.
- `chat_data/dedupe_jsonl.py`: deduplicates incremental ChatGPT JSONL by `message_id`.
- `import_scripts/build_sqlite_fts.py`: builds `chat_data/chat_search.db` from both JSONL sources.
- `servers/app.py`: FastAPI API server for `/search`, `/search_by_date`, summary lookup endpoints, wishes, and `/healthz`.
- `servers/search_sqlite.py`: SQLite + FTS5 backend used by the API server by default.
- `servers/mcp_search_supabase.py` and `servers/server.py`: MCP wrappers that call the HTTP API. They can keep working as long as the API endpoints stay the same.

Legacy Supabase code is still present for reference:

- `servers/search_supabase.py`
- `import_scripts/import_supabase.py`
- `import_scripts/import_supabase_simple.py`
- `chat_data/generate_supabase_sql.py`

## Build The SQLite DB

```powershell
python chat_data\dedupe_jsonl.py
python import_scripts\build_sqlite_fts.py
```

The builder reads:

- `chat_data/cleaned_chats.jsonl`
- `chat_data/chatgpt_incremental_deduped.jsonl`

and writes:

- `chat_data/chat_search.db`

## Import Core Anchors

Core Anchors are short, high-signal reset sentences for context injection. They
live in a separate `core_anchors` table instead of `messages`, because they are
curated boot material rather than chat history.

```powershell
python import_scripts\import_core_anchors.py --dry-run
python import_scripts\import_core_anchors.py
```

By default the importer reads:

```text
C:\Users\ellat\Documents\KM-backup\Kai-Mei-Memory-Vault\Core Anchors V0.1.md
```

and writes:

```text
chat_data\chat_search_rebuilt.db
```

The import is idempotent: rows are upserted by `anchor_key`, so rerunning the
script updates existing anchors instead of creating duplicates.

Recommended retrieval shape:

- Boot context: select `status='active'`, `function='boot_core'`, ordered by
  `priority ASC`.
- Panic/grounding context: select `function='soothe_panic'`, ordered by
  `priority ASC`.
- Nice-to-have context: select `function='boot_nice_to_have'` only when there is
  spare context budget.
- Keyword search should be auxiliary. The main selector should stay
  deterministic by `function`, `status`, and `priority`.

Core Anchors can also be read through the API and MCP wrapper:

```text
GET /core_anchors?function=boot_core&limit=8
GET /core_anchors?function=soothe_panic&limit=8
GET /core_anchors?q=戒指&limit=5
```

The MCP tool is:

- `get_core_anchors`

## Unified World Book

The API can merge these lorebook sources in stable order:

```text
memory_system.json
Expansion_Pack_v2.json
Recent_Updates.json
```

Configure their directory with `KMLOG_WORLDBOOK_DIR`. During local development,
the default also detects a sibling `chat-proxy/world_book` directory. The merged
output defaults to `World_Book_Merged.json`; it is generated data and should not
be edited directly. `Recent_Updates.json` is the only writable source.

HTTP endpoints:

```text
GET  /worldbook/source
GET  /worldbook/entries?q=keyword&enabled=true&limit=100
POST /worldbook/updates/preview
POST /worldbook/updates/apply
POST /worldbook/rebuild
```

Writes use `KMLOG_WORLDBOOK_WRITE_TOKEN`, falling back to `SEARCH_API_TOKEN`.
They require the current SHA-256 revision. Preview returns a unified diff without
writing; apply backs up `Recent_Updates.json`, atomically replaces it, and rebuilds
the merged file. Supported operations are `create_entry`, `replace_entry`,
`patch_entry`, and `set_enabled`. Deletion is intentionally not exposed.

MCP wrappers expose:

- `get_worldbook_source`
- `list_worldbook_entries`
- `preview_worldbook_update`
- `apply_worldbook_update`
- `rebuild_worldbook`

Both MCP entrypoints accept the registered bare tool names and the connector-
namespaced form `kmlog_search.<tool>`. Namespaced dispatch is restricted to the
`kmlog_search` / `kmlog-search` prefixes and already registered tool names.

Example update:

```json
{
  "expected_revision": "sha256:...",
  "operations": [
    {
      "op": "patch_entry",
      "id": "wb-entry-id",
      "changes": {
        "content": "Updated content",
        "keywords": ["keyword"]
      }
    }
  ],
  "actor": "manual-review"
}
```

## Import Mother Markdown Memory

The current mother memory file can be ingested into lightweight SQLite tables
for exact section lookup and keyword search. This is intentionally not semantic
RAG and does not auto-inject anything into `chat-proxy` / `context_builder`.

Input:

```text
mother\记忆库(Current).md
```

Backing tables:

```sql
memory_mother_sections(path, title, level, content, source_file, updated_at)
memory_mother_toc(path, title, parent_path, order_index)
memory_mother_write_log(before_revision, after_revision, operations_json, actor, created_at)
```

These mother memory tables intentionally do not use the raw-turn `kind`
classification from `messages`.

The API lazily refreshes these tables when the markdown source file changes.
HTTP endpoints:

```text
GET /memory/toc
GET /memory/source
GET /memory/section?path=F.2
GET /memory/section?path=I-1&include_children=true
GET /memory/search?q=canonical&scope=F
POST /memory/route
POST /memory/updates/preview
POST /memory/updates/apply
```

MCP wrappers expose matching read and controlled-write tools:

- `get_mother_toc`
- `get_mother_source`
- `get_mother_section`
- `search_mother_memory`
- `route_mother_memory`
- `preview_mother_memory_update`
- `apply_mother_memory_update`

The Markdown file remains the source of truth; SQLite mother tables are derived
read caches. Writes use a SHA-256 `expected_revision`, validate the complete
result, create a timestamped backup under `mother/.backups`, atomically replace
the Markdown file, refresh SQLite, and append an audit row to
`memory_mother_write_log`. Supported operations are `replace_content`,
`append_content`, `update_title`, and `create_section`. See
[`docs/mother_memory_write_workflow.md`](docs/mother_memory_write_workflow.md).

`/memory/route` is a deterministic intent router, not RAG. It suggests section
paths and returns matching section rows with `inject: false`:

```json
{
  "query": "她怕打雷怎么哄",
  "mode": "auto",
  "task_hint": null
}
```

By default, `get_mother_section` returns only the exact stored section content.
Pass `include_children=true` to recursively merge all descendant sections in
table-of-contents order. The expanded response keeps the section's exact text in
`own_content`, returns the merged Markdown in `content`, and lists its provenance
in `included_paths`. Descendant headings are normalized to include their complete
section paths.

If a routed top-level section has empty content, the router returns its direct
child sections instead.

The initial intent map is:

```python
{
  "health": ["C", "F.2"],
  "panic": ["C", "F.4", "G"],
  "infra": ["D.3"],
  "ritual": ["D.2", "G", "H"],
  "setting": ["H"],
  "profile": ["A", "B"],
  "rules": ["F"],
}
```

During import, rows that do not already have `kind` are auto-classified with a
small heuristic. This applies to raw chat rows in `messages`, not to
`memory_mother_sections`:

- `chat`: normal messages.
- `summary`: explicit summary artifacts, including numbered summary cards.
- `meta`: infra/import/database/search/summary-mechanism discussion.
- `noise`: HTTP errors, tracebacks, connection failures, and similar noise.

## Run The API

```powershell
uvicorn servers.app:app --host 127.0.0.1 --port 8013
```

Open the local wishes page at:

```text
http://127.0.0.1:8013/wishes
```

Open the weekly memory candidate review page at:

```text
http://127.0.0.1:8013/memory_week
```

The review page writes candidate decisions back to SQLite. `Accept`, `Reject`,
`Defer`, and `Reset` update `daily_memory_candidates.status`; `Promote` opens an
edit form and writes a curated row to `reviewed_memory_items`.

Open the reviewed memory browser at:

```text
http://127.0.0.1:8013/reviewed_memory
```

The reviewed memory page lists curated items, supports status/domain/function/
mother/keyword filtering, can include provenance rows, and can reverse lookup
reviewed items by `messages.id` or external `messages.message_id`.

Useful environment variables:

- `SEARCH_API_TOKEN`: optional API token for HTTP requests.
- `KMLOG_MOTHER_WRITE_TOKEN`: optional dedicated token for mother Markdown
  preview/apply calls; falls back to `SEARCH_API_TOKEN`.
- `KMLOG_WORLDBOOK_DIR`: directory containing the three World Book JSON sources.
- `KMLOG_WORLDBOOK_WRITE_TOKEN`: optional dedicated token for World Book writes;
  falls back to `SEARCH_API_TOKEN`.
- `KMLOG_SEARCH_BACKEND`: `sqlite` by default. Set to `supabase` only for the legacy backend.
- `KMLOG_SQLITE_DB`: optional custom path to the SQLite database.
- `KMLOG_DISABLE_DOCS`: set to `1` on public deployments to disable `/docs`, `/redoc`, and `/openapi.json`.

## API

```text
GET  /healthz
GET  /conversation_summary
GET  /core_anchors
GET  /daily_memory_candidates
GET  /daily_summaries
GET  /daily_summary
GET  /memory/search
GET  /memory/section
GET  /memory/source
GET  /memory/toc
GET  /memory_week
GET  /reviewed_memory
GET  /reviewed_memory/by_message
GET  /reviewed_memory_items
GET  /weekly_memory_candidates
GET  /worldbook/entries
GET  /worldbook/source
GET  /wish
GET  /wishes
POST /complete_wish/{id}
POST /memory_candidates/{id}/status
POST /memory_candidates/promote
PATCH /reviewed_memory_items/{id}
POST /reviewed_memory_items/{id}/status
POST /memory/route
POST /memory/updates/preview
POST /memory/updates/apply
POST /search
POST /search_by_date
POST /worldbook/rebuild
POST /worldbook/updates/apply
POST /worldbook/updates/preview
POST /wish
POST /wish/{id}/status
POST /ensure_indexes
```

`/search` request:

```json
{
  "query": "keyword",
  "limit": 10,
  "mode": "auto",
  "kinds": ["chat"],
  "after": "2026-01-01",
  "before": "2026-05-01"
}
```

`GET /wish` query parameters:

```text
id=12
q=keyword
owner=Mei|Kai|Shared
scope=care|work|romance|play|misc
status=open|done|stale|archived
limit=50
```

All filters are optional. `q` searches wish text and tags.

`POST /wish` request:

```json
{
  "owner": "Mei",
  "scope": "care",
  "text": "short wish text",
  "status": "open",
  "priority": 3,
  "tags": "foo,bar",
  "source": "manual"
}
```

`POST /complete_wish/{id}` marks a wish as `done` and returns the updated row.

`POST /wish/{id}/status` request:

```json
{
  "status": "stale"
}
```

Allowed status values are `open`, `done`, `stale`, and `archived`.

If `SEARCH_API_TOKEN` is set, enter the same token in the `/wishes` page token
field. The page stores it in browser local storage and sends it as `X-API-Key`.

`kinds`, `after`, and `before` are optional. If `kinds` is omitted, `/search`
defaults to `["chat"]`.

`/search_by_date` request:

```json
{
  "start_date": "2026-04-29",
  "end_date": "2026-04-30",
  "role": "user",
  "limit": 20
}
```

Summary lookup endpoints:

```text
GET /daily_summary?date_key=2026-05-11
GET /daily_summaries?start_date=2026-05-01&end_date=2026-05-18&limit=20
GET /daily_memory_candidates?date_key=2026-05-11&status=candidate&limit=50
GET /weekly_memory_candidates?start_date=2026-05-18&end_date=2026-05-24&status=candidate&limit=100
GET /reviewed_memory_items?function=boot_core&status=active&limit=20
GET /reviewed_memory/by_message?message_pk=30097
GET /conversation_summary?conversation_id=<conversation_id>
```

Daily summaries and raw memory candidates are generated upstream by
`chat-proxy`, not by this FastAPI process. This repo treats
`daily_summaries`, `daily_summary_versions`, and `daily_memory_candidates` as
read/review inputs.

Preferred manual backfill entrypoint:

```powershell
cd C:\Users\ellat\Desktop\K_Space\chat-proxy
python -m chat_proxy.manual_daily_summary --date 2026-05-18
python -m chat_proxy.manual_daily_summary --date 2026-05-18 --force
```

Preferred HTTP entrypoint from `chat-proxy`:

```json
POST /daily-summaries/run
{
  "date_key": "2026-05-18",
  "force": false
}
```

Cron should normally call the `chat-proxy` runner, then use this service for
weekly candidate review, promotion, and provenance lookup.

Detailed runbook:

```text
docs/reviewed_memory_workflow.md
```

`/weekly_memory_candidates` reads the same daily candidate table, then returns
deduped weekly groups. The dedupe key is conservative: domain, function,
primary mother, secondary mother, normalized label, and a short normalized
evidence prefix. Each group includes a best `canonical` candidate, source
candidate IDs, source dates, duplicate count, labels, and evidence snippets.

Candidate review states are stored on `daily_memory_candidates.status`:

- `candidate`: machine-generated proposal, not reviewed yet.
- `accepted`: a human marked it as useful, but it has not been materialized.
- `rejected`: a human explicitly rejected it.
- `deferred`: review postponed.
- `merged`: folded into another candidate or reviewed item.
- `superseded`: replaced by a later candidate/review decision.
- `promoted`: materialized into `reviewed_memory_items`.

Reviewed memory items are the curated layer intended for later context
retrieval. They are separate from raw candidates:

```sql
reviewed_memory_items(
  id, title, content, evidence,
  domain, function, primary_mother, secondary_mother,
  topic_key, layer_role, canonical_ref,
  importance, confidence, explicitness,
  status, source_candidate_ids_json, source_message_ids_json,
  reviewer, reviewed_at, created_at, updated_at,
  expires_at, review_after, superseded_by_item_id, metadata_json
)

reviewed_memory_sources(
  id, memory_item_id, candidate_id, message_pk, message_id,
  evidence, source_role, created_at
)
```

Optional reviewed-memory representation fields are nullable and are not
backfilled for existing rows:

- `topic_key`: stable semantic topic identifier.
- `layer_role`: representation role, for example `retrieval_summary`,
  `canonical_rule`, `boot_anchor`, `event_evidence`, or `temporary_project_state`.
- `canonical_ref`: pointer to a canonical source, for example `mother:F.4.4`.
- `review_after`: date for re-evaluating non-durable memory.

`reviewed_memory_items.status` is limited to the curated-item lifecycle:
`active`, `archived`, or `superseded`. Expired items are excluded from normal
lookup unless `include_expired=true`.

Promotion accepts one or more candidate IDs and optional rewritten fields:

```json
{
  "candidate_ids": [8],
  "title": "Curated memory title",
  "content": "Human-edited memory text for context retrieval.",
  "explicitness": "edited_by_human",
  "reviewer": "human"
}
```

The promoted item keeps candidate/message provenance in JSON snapshot fields and
in `reviewed_memory_sources`, so callers can reverse lookup curated memories by
either `messages.id` (`message_pk`) or external `messages.message_id`.

The MCP wrappers expose matching tools:

- `get_daily_summary`
- `list_daily_summaries`
- `get_daily_memory_candidates`
- `get_weekly_memory_candidates`
- `update_memory_candidate_status`
- `promote_memory_candidate`
- `get_reviewed_memory_items`
- `update_reviewed_memory_item`
- `update_reviewed_memory_status`
- `get_reviewed_memory_by_message`
- `get_conversation_summary`

## VPS Notes

Keep the public HTTP API stable and swap only the backend:

```text
MCP client -> FastAPI /search -> SQLite FTS5
```

That means the already-deployed MCP wrappers do not need a big rewrite if they call the same `/search` and `/search_by_date` endpoints.

For public deployment, keep Uvicorn bound to localhost and put Nginx in front:

```nginx
server {
    listen 443 ssl;
    server_name example.com;

    auth_basic "KMLog";
    auth_basic_user_file /etc/nginx/.htpasswd-kmlog;

    location / {
        proxy_pass http://127.0.0.1:8013;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

If KMLog shares a host under a subpath such as `/kmlog/`, strip that prefix
when proxying to Uvicorn:

```nginx
location /kmlog/ {
    proxy_pass http://127.0.0.1:8013/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

With that shape, public URLs look like:

```text
https://example.com/kmlog/memory_week
https://example.com/kmlog/wishes
```

Set MCP clients to the same public base path:

```text
KMLOG_BASE_URL=https://example.com/kmlog
KMLOG_API_KEY=<same value as SEARCH_API_TOKEN>
```

Create the password file with:

```bash
sudo htpasswd -c /etc/nginx/.htpasswd-kmlog mei
```

On a public host, also set a long random `SEARCH_API_TOKEN` and `KMLOG_DISABLE_DOCS=1`.
The API sends `X-Robots-Tag: noindex, nofollow, noarchive`, and `/robots.txt`
disallows crawling, but authentication remains the real security boundary.

