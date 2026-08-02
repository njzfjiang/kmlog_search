# Mother Memory Write Workflow

The mother Markdown file is the durable source of truth. The
`memory_mother_sections` and `memory_mother_toc` tables are derived read caches
and must not be edited directly.

## Safe write flow

1. Call `GET /memory/source` and retain its SHA-256 `revision`.
2. Send operations to `POST /memory/updates/preview` with that value as
   `expected_revision`.
3. Review the returned unified diff and warnings.
4. Send the same revision and operations to `POST /memory/updates/apply`.
5. If the source changed meanwhile, refresh and retry after the API returns
   `409 Conflict`.

Preview and apply accept at most 100 operations. Preview never writes the file.
Apply validates the full result, creates a timestamped backup in
`mother/.backups`, atomically replaces the source, refreshes the SQLite cache,
and records the revision pair and operations in `memory_mother_write_log`.

## Request format

```json
{
  "expected_revision": "sha256:...",
  "actor": "weekly-review",
  "operations": [
    {
      "op": "replace_content",
      "path": "F.4.4",
      "content": "Replacement own content."
    },
    {
      "op": "append_content",
      "path": "A.4.1",
      "content": "[2026-08-02｜#TBD] New record."
    },
    {
      "op": "update_title",
      "path": "D.3",
      "title": "技术与记忆基础设施"
    },
    {
      "op": "create_section",
      "path": "F.4.5",
      "parent_path": "F.4",
      "after_path": "F.4.4",
      "title": "New section",
      "content": "Initial content."
    }
  ]
}
```

`replace_content` and `append_content` affect only the exact section's own
content and preserve descendants. `update_title` does not rename the path.
`create_section` requires a new path whose direct parent exists; `after_path`,
when provided, must be a sibling. New headings use explicit full paths.

Delete, path rename, subtree move, automatic renumbering, and upsert operations
are intentionally not supported.

## Authentication

Preview and apply use `KMLOG_MOTHER_WRITE_TOKEN`. When it is unset, they fall
back to `SEARCH_API_TOKEN`; both may be empty for local development. Keep the
write token out of browser local storage and public client configuration.

MCP exposes:

- `get_mother_source`
- `preview_mother_memory_update`
- `apply_mother_memory_update`

The MCP write tools read `KMLOG_MOTHER_WRITE_TOKEN` and otherwise use their
normal KMLog API token.

## Reverse proxy

Public deployments need both the existing read routes and the write prefix:

```nginx
location ^~ /memory/updates/ {
    proxy_pass http://127.0.0.1:8013;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

Keep `GET /memory/source` routed to the same FastAPI service.
