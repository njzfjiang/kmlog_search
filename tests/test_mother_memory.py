import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVERS_DIR = PROJECT_ROOT / "servers"
if str(SERVERS_DIR) not in sys.path:
    sys.path.insert(0, str(SERVERS_DIR))

import search_sqlite  # noqa: E402


def test_mother_markdown_ingestion_and_search(tmp_path, monkeypatch):
    db_path = tmp_path / "chat_search.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    mother_path = tmp_path / "mother.md"
    mother_path.write_text(
        """
---
last updated: 2026-06-07
---
### F. Canonical Layer
F intro canonical text.

#### F.1 First
Alpha canonical detail.

#### F.2 Second
Beta canonical detail.

### G. Other Layer
Gamma canonical detail.

### C. Health & care

#### C.1 Overview
Health overview.

### D. Life & assets
Life canonical detail.

#### D.3 技术 & 记忆 infra
Infra canonical detail.

### I-1. Deep Intimacy （深度亲密/成人向）(Preferences)
#### 1. 总体偏好（氛围向）
Preference canonical detail.
#### 2. 触碰偏好（轻～中程度，未来可加细）
Touch canonical detail.

### I-2. Deep Intimacy （深度亲密/成人向）(Consent & Guardrail)
#### 1. 同意 & 安全闸（重要）
Consent canonical detail.

#### I-3. Deep Intimacy （深度亲密/成人向）(Recent Records)
Recent canonical detail.
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(search_sqlite, "DB_PATH", db_path)
    monkeypatch.setattr(search_sqlite, "DEFAULT_MOTHER_MEMORY_PATH", mother_path)

    result = search_sqlite.ingest_mother_markdown()
    assert result["section_count"] == 14

    toc = search_sqlite.list_mother_toc()
    assert [item["path"] for item in toc] == [
        "F",
        "F.1",
        "F.2",
        "G",
        "C",
        "C.1",
        "D",
        "D.3",
        "I-1",
        "I-1.1",
        "I-1.2",
        "I-2",
        "I-2.1",
        "I-3",
    ]
    assert toc[2]["parent_path"] == "F"
    assert toc[9]["parent_path"] == "I-1"
    assert toc[12]["parent_path"] == "I-2"

    section = search_sqlite.get_mother_section("F.2")
    assert section["title"] == "Second"
    assert "Beta canonical detail." in section["content"]
    assert search_sqlite.get_mother_section("I-1.1")["title"] == "总体偏好（氛围向）"
    assert search_sqlite.get_mother_section("I-3")["title"] == "Deep Intimacy （深度亲密/成人向）(Recent Records)"

    exact_i = search_sqlite.get_mother_section("I-1")
    assert exact_i["content"] == ""
    assert "own_content" not in exact_i

    expanded_i = search_sqlite.get_mother_section(
        "I-1",
        include_children=True,
    )
    assert expanded_i["own_content"] == ""
    assert expanded_i["included_paths"] == ["I-1", "I-1.1", "I-1.2"]
    assert "#### I-1.1. 总体偏好（氛围向）" in expanded_i["content"]
    assert "Preference canonical detail." in expanded_i["content"]
    assert "#### I-1.2. 触碰偏好（轻～中程度，未来可加细）" in expanded_i["content"]
    assert "Touch canonical detail." in expanded_i["content"]

    scoped = search_sqlite.search_mother_sections("canonical", scope="F")
    assert [item["path"] for item in scoped] == ["F", "F.1", "F.2"]
    scoped_i = search_sqlite.search_mother_sections("canonical", scope="I")
    assert [item["path"] for item in scoped_i] == [
        "I-1.1",
        "I-1.2",
        "I-2.1",
        "I-3",
    ]

    route = search_sqlite.route_mother_memory("她怕打雷怎么哄")
    assert route["inject"] is False
    assert route["suggested_paths"] == ["C", "F.2"]
    assert route["routes"][0]["reason"] == "health keyword: 打雷"
    assert [section["path"] for section in route["sections"][:2]] == ["C.1", "F.2"]

    loss_route = search_sqlite.route_mother_memory("模型下架会不会消失")
    assert loss_route["suggested_paths"] == ["C", "F.4", "G"]

    infra_route = search_sqlite.route_mother_memory("deploy endpoint", mode="infra")
    assert infra_route["suggested_paths"] == ["D.3"]


def test_mother_markdown_refreshes_legacy_cache_without_parser_meta(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "chat_search.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    mother_path = tmp_path / "mother.md"
    mother_path.write_text(
        """
### A. Alpha
Alpha text.

### I-1. Deep Intimacy （深度亲密/成人向）(Preferences)
#### 1. 总体偏好（氛围向）
Preference text.
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(search_sqlite, "DB_PATH", db_path)
    monkeypatch.setattr(search_sqlite, "DEFAULT_MOTHER_MEMORY_PATH", mother_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    search_sqlite.ensure_mother_memory_tables(cursor)
    cursor.execute("DELETE FROM memory_mother_meta")
    cursor.execute(
        """
        INSERT INTO memory_mother_sections
            (path, title, level, content, source_file, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("A", "Alpha", 3, "Alpha text.", str(mother_path), "same-mtime"),
    )
    cursor.execute(
        """
        INSERT INTO memory_mother_toc
            (path, title, parent_path, order_index)
        VALUES (?, ?, ?, ?)
        """,
        ("A", "Alpha", None, 0),
    )
    conn.commit()
    conn.close()

    toc = search_sqlite.list_mother_toc()
    assert [item["path"] for item in toc] == ["A", "I-1", "I-1.1"]


def test_get_mother_section_recursively_merges_nested_descendants(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "chat_search.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    mother_path = tmp_path / "mother.md"
    mother_path.write_text(
        """
### A. Parent
Parent text.

#### 1. Child
Child text.

##### 1. Grandchild
Grandchild text.
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(search_sqlite, "DB_PATH", db_path)
    monkeypatch.setattr(search_sqlite, "DEFAULT_MOTHER_MEMORY_PATH", mother_path)

    expanded = search_sqlite.get_mother_section("A", include_children=True)

    assert expanded["own_content"] == "Parent text."
    assert expanded["included_paths"] == ["A", "A.1", "A.1.1"]
    assert expanded["content"].index("Parent text.") < expanded["content"].index(
        "#### A.1. Child"
    )
    assert expanded["content"].index("#### A.1. Child") < expanded[
        "content"
    ].index("##### A.1.1. Grandchild")
    assert "Grandchild text." in expanded["content"]
