"""Tests for DatabaseManager and GraphIgnoreFilter (ADR-002)."""

from pathlib import Path

from knowledge_graph.indexer import TABLE_NAMES, DatabaseManager, GraphIgnoreFilter


def test_schema_created_in_memory() -> None:
    with DatabaseManager(":memory:") as db:
        assert sorted(db.table_names()) == sorted(TABLE_NAMES)


def test_schema_idempotent_on_file(tmp_path: Path) -> None:
    db_file = tmp_path / "sub" / "graph.db"
    with DatabaseManager(db_file) as db:
        db.connection.execute(
            "INSERT INTO commits (hash, timestamp, message) VALUES ('abc', 1, 'm')"
        )
        db.connection.commit()
    with DatabaseManager(db_file) as db:  # reopening must not drop data
        assert db.connection.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 1


def test_graphignore_parsing_and_matching(tmp_path: Path) -> None:
    ignore_file = tmp_path / ".graphignore"
    ignore_file.write_text(
        "# lockfiles\n*.lock\n\ndist/\n./build/*.js\nsrc/generated_*.py\n"
    )
    f = GraphIgnoreFilter(ignore_file)
    assert f.patterns == ["*.lock", "dist/", "build/*.js", "src/generated_*.py"]
    assert f.is_ignored("package-lock.lock")
    assert f.is_ignored("deps/poetry.lock")
    assert f.is_ignored("dist/bundle.js")
    assert f.is_ignored("dist/nested/x.map")
    assert f.is_ignored("build/app.js")
    assert f.is_ignored("src/generated_models.py")
    assert not f.is_ignored("src/models.py")
    assert not f.is_ignored("distribution/readme.md")


def test_missing_graphignore_ignores_nothing(tmp_path: Path) -> None:
    f = GraphIgnoreFilter.for_target(tmp_path)
    assert f.patterns == []
    assert not f.is_ignored("anything.lock")
