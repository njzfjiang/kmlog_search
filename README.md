# KMLog Search

Self-hosted chat log search using SQLite + FTS5, with a FastAPI HTTP API and FastMCP wrappers.

## What Is Reusable Here

- `chat_data/clean.py`: extracts the older `chats.json` export format into `cleaned_chats.jsonl`.
- `chat_data/import_chats.py`: extracts the newer `conversations.json` export format into `chatgpt_incremental.jsonl`.
- `chat_data/dedupe_jsonl.py`: deduplicates incremental ChatGPT JSONL by `message_id`.
- `import_scripts/build_sqlite_fts.py`: builds `chat_data/chat_search.db` from both JSONL sources.
- `servers/app.py`: FastAPI API server for `/search`, `/search_by_date`, and `/healthz`.
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

## Run The API

```powershell
uvicorn servers.app:app --host 127.0.0.1 --port 8013
```

Open the local wishes page at:

```text
http://127.0.0.1:8013/wishes
```

Useful environment variables:

- `SEARCH_API_TOKEN`: optional API token for HTTP requests.
- `KMLOG_SEARCH_BACKEND`: `sqlite` by default. Set to `supabase` only for the legacy backend.
- `KMLOG_SQLITE_DB`: optional custom path to the SQLite database.
- `KMLOG_DISABLE_DOCS`: set to `1` on public deployments to disable `/docs`, `/redoc`, and `/openapi.json`.

## API

```text
GET  /healthz
GET  /wish
GET  /wishes
POST /complete_wish/{id}
POST /search
POST /search_by_date
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

Create the password file with:

```bash
sudo htpasswd -c /etc/nginx/.htpasswd-kmlog mei
```

On a public host, also set a long random `SEARCH_API_TOKEN` and `KMLOG_DISABLE_DOCS=1`.
The API sends `X-Robots-Tag: noindex, nofollow, noarchive`, and `/robots.txt`
disallows crawling, but authentication remains the real security boundary.
