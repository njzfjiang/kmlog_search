import sqlite3

from servers.message_search import _excerpts, _terms, search_with_evidence


def test_single_cjk_query_is_preserved():
    assert _terms("药", []) == ["药"]
    assert _terms("药 药", ["药"]) == ["药"]
    assert _terms("%", []) == []


def test_overlapping_excerpt_windows_are_merged():
    content = "x" * 1000
    spans = [{"start": 100, "end": 120}, {"start": 112, "end": 120},
             {"start": 300, "end": 320}]
    excerpts = _excerpts(content, spans)
    assert excerpts == [{"start": 40, "end": 480, "text": content[40:480]}]


def test_excerpt_selection_preserves_term_priority_and_offsets():
    content = "x" * 2000
    spans = [{"start": 1500, "end": 1520}, {"start": 10, "end": 20},
             {"start": 500, "end": 510}, {"start": 800, "end": 820}]
    excerpts = _excerpts(content, spans)
    assert [e["start"] for e in excerpts] == [1440, 0, 440]
    assert all(e["text"] == content[e["start"]:e["end"]] for e in excerpts)


def test_body_pool_rescues_late_match_from_title_only_candidates(tmp_path):
    path = tmp_path / "messages.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE messages (id INTEGER, timestamp TEXT, role TEXT, content TEXT, conversation_title TEXT, kind TEXT)")
    rows = [(i, "2026-09-01", "user", "unrelated", "FISTA project", "chat") for i in range(1, 81)]
    rows += [(99, "2026-08-01", "user", "prefix " * 80 + "FISTA converged", "experiment", "chat"),
             (100, "2026-10-01", "user", "FISTA future", "", "chat"),
             (101, "2026-08-01", "user", "FISTA summary", "", "summary")]
    conn.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()

    def legacy(query, **kwargs):
        return [(r[0], r[1], r[2], r[3][:160], r[4], 1.2, "title") for r in rows[:40]], {}

    result = search_with_evidence("FISTA", legacy_search=legacy,
        connection_factory=lambda: sqlite3.connect(path), limit=2, before="2026-09-07")
    first = result["results"][0]
    assert first["id"] == 99
    assert "FISTA" not in first["content_preview"]
    assert "FISTA" in first["matched_excerpt"]
    assert first["body_matched_terms"] == ["FISTA"]
    assert first["match_spans"][0]["start"] == 560
    assert 100 not in result["candidate_ids"]
    assert 101 not in result["candidate_ids"]


def test_entities_and_like_wildcards_are_literal(tmp_path):
    path = tmp_path / "messages.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE messages (id INTEGER, timestamp TEXT, role TEXT, content TEXT, conversation_title TEXT, kind TEXT)")
    conn.executemany("INSERT INTO messages VALUES (?, '2026-01-01', 'user', ?, '', 'chat')",
                     [(1, "FISTA"), (2, "ISTA"), (3, "score 50%"), (4, "score 500"),
                      (5, "今天按时吃药")])
    conn.commit()
    conn.close()
    def search(query):
        return search_with_evidence(query, legacy_search=lambda *a, **k: ([], {}),
                                   connection_factory=lambda: sqlite3.connect(path))
    result = search("ISTA")
    assert result["results"][0]["id"] == 2
    assert next(r for r in result["results"] if r["id"] == 1)["body_matched_terms"] == []
    assert [r["id"] for r in search("50%")["results"]] == [3]
    medicine = search("药")["results"]
    assert [r["id"] for r in medicine] == [5]
    assert medicine[0]["body_matched_terms"] == ["药"]
    assert "药" in medicine[0]["matched_excerpt"]
