import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path(
    r"C:\Users\ellat\Documents\KM-backup\Kai-Mei-Memory-Vault\Core Anchors V0.1.md"
)
DEFAULT_DB = PROJECT_ROOT / "chat_data" / "chat_search.db"
EXPECTED_COLUMNS = [
    "anchor_key",
    "title",
    "content",
    "domain",
    "function",
    "primary_mother",
    "secondary_mother",
    "priority",
    "status",
    "source_anchor_ids",
]


def clean_cell(value: str) -> str:
    value = (value or "").strip()
    if value.startswith("`") and value.endswith("`") and len(value) >= 2:
        value = value[1:-1]
    return value.strip()


def split_markdown_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [clean_cell(cell) for cell in line.split("|")]


def is_separator_row(cells: list[str]) -> bool:
    return all(cell.replace(":", "").replace("-", "").strip() == "" for cell in cells)


def parse_core_anchors(path: Path) -> list[dict]:
    rows = []
    header = None

    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue

        cells = split_markdown_row(stripped)
        if not cells:
            continue

        if header is None:
            if cells == EXPECTED_COLUMNS:
                header = cells
            continue

        if is_separator_row(cells):
            continue

        if len(cells) != len(header):
            raise ValueError(
                f"{path}:{line_number} expected {len(header)} cells, got {len(cells)}"
            )

        row = dict(zip(header, cells))
        row["priority"] = int(row["priority"])
        row["importance"] = 5
        row["source_dates"] = ""
        row["evidence"] = ""
        row["metadata_json"] = json.dumps(
            {
                "imported_from": str(path),
                "import_line": line_number,
                "domain_raw": row["domain"],
                "function_raw": row["function"],
            },
            ensure_ascii=False,
        )
        rows.append(row)

    if header is None:
        raise ValueError(f"Could not find core anchors markdown table in {path}")
    if not rows:
        raise ValueError(f"No core anchors found in {path}")

    keys = [row["anchor_key"] for row in rows]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"Duplicate anchor_key values: {', '.join(duplicates)}")

    return rows


def ensure_core_anchors_table(cursor: sqlite3.Cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS core_anchors (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          anchor_key TEXT UNIQUE NOT NULL,
          title TEXT NOT NULL,
          content TEXT NOT NULL,
          domain TEXT NOT NULL,
          function TEXT NOT NULL,
          primary_mother TEXT NOT NULL,
          secondary_mother TEXT,
          importance INTEGER DEFAULT 5,
          priority INTEGER DEFAULT 3,
          status TEXT DEFAULT 'active',
          source_anchor_ids TEXT,
          source_dates TEXT,
          evidence TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          metadata_json TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_core_anchors_status ON core_anchors(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_core_anchors_priority ON core_anchors(priority)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_core_anchors_function ON core_anchors(function)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_core_anchors_primary_mother ON core_anchors(primary_mother)")


def import_core_anchors(db_path: Path, source_path: Path, dry_run: bool = False) -> list[dict]:
    anchors = parse_core_anchors(source_path)
    if dry_run:
        return anchors

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        ensure_core_anchors_table(cursor)
        for anchor in anchors:
            cursor.execute(
                """
                INSERT INTO core_anchors (
                    anchor_key,
                    title,
                    content,
                    domain,
                    function,
                    primary_mother,
                    secondary_mother,
                    importance,
                    priority,
                    status,
                    source_anchor_ids,
                    source_dates,
                    evidence,
                    created_at,
                    updated_at,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(anchor_key) DO UPDATE SET
                    title = excluded.title,
                    content = excluded.content,
                    domain = excluded.domain,
                    function = excluded.function,
                    primary_mother = excluded.primary_mother,
                    secondary_mother = excluded.secondary_mother,
                    importance = excluded.importance,
                    priority = excluded.priority,
                    status = excluded.status,
                    source_anchor_ids = excluded.source_anchor_ids,
                    source_dates = excluded.source_dates,
                    evidence = excluded.evidence,
                    updated_at = excluded.updated_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    anchor["anchor_key"],
                    anchor["title"],
                    anchor["content"],
                    anchor["domain"],
                    anchor["function"],
                    anchor["primary_mother"],
                    anchor["secondary_mother"] or None,
                    anchor["importance"],
                    anchor["priority"],
                    anchor["status"],
                    anchor["source_anchor_ids"],
                    anchor["source_dates"],
                    anchor["evidence"],
                    now,
                    now,
                    anchor["metadata_json"],
                ),
            )
        conn.commit()
    finally:
        conn.close()

    return anchors


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Core Anchors markdown table into SQLite.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help=f"Markdown source. Default: {DEFAULT_SOURCE}")
    parser.add_argument("--db", default=str(DEFAULT_DB), help=f"SQLite DB path. Default: {DEFAULT_DB}")
    parser.add_argument("--dry-run", action="store_true", help="Parse and print rows without writing.")
    args = parser.parse_args()

    source_path = Path(args.source)
    db_path = Path(args.db)
    if not source_path.exists():
        raise RuntimeError(f"Source file not found: {source_path}")
    if not args.dry_run and not db_path.exists():
        raise RuntimeError(f"SQLite DB not found: {db_path}")

    anchors = import_core_anchors(db_path, source_path, dry_run=args.dry_run)
    print(f"Parsed core anchors: {len(anchors)}")
    if args.dry_run:
        for anchor in anchors:
            print(f"- {anchor['priority']} {anchor['anchor_key']}: {anchor['title']}")
    else:
        print(f"Imported into: {db_path}")


if __name__ == "__main__":
    main()
