"""Tests for GitParser, incremental/self-healing indexing, and Jaccard math (ADR-003)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from knowledge_graph import indexer as mod
from knowledge_graph.indexer import (
    RS,
    US,
    DatabaseManager,
    GitLogError,
    GitParser,
    GraphIgnoreFilter,
    Indexer,
)


def record(h: str, ts: int, message: str, files: list[str]) -> str:
    """Build one raw git-log record exactly as the ADR-003 format emits it."""
    body = message + "\n"
    tail = "\n" + "\n".join(files) + "\n" if files else ""
    return f"{RS}{h}{US}{ts}{US}{body}{tail}"


class FakeGit:
    """Scripted stand-in for subprocess.run; records argv and returns/raises per call."""

    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses: Iterator[str | Exception] = iter(responses)
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return subprocess.CompletedProcess(cmd, 0, stdout=response, stderr="")


@pytest.fixture
def fake_git(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[str | Exception]], FakeGit]:
    def _install(responses: list[str | Exception]) -> FakeGit:
        fake = FakeGit(responses)
        monkeypatch.setattr(mod.subprocess, "run", fake)
        return fake

    return _install


def make_indexer(tmp_path: Path, patterns: list[str] | None = None) -> tuple[Indexer, DatabaseManager]:
    db = DatabaseManager(":memory:")
    ignore = GraphIgnoreFilter.from_patterns(patterns or [])
    return Indexer(tmp_path, db, ignore=ignore), db


# ----------------------------------------------------------------- parsing


def test_parse_records_oldest_first_with_spec_id_and_files() -> None:
    raw = record("h2", 200, "feat(x): second [ADR-003]\n\nlonger body", ["a.py", "b.py"]) + record(
        "h1", 100, "chore: first", ["a.py"]
    )
    commits = GitParser.parse(raw)
    assert [c.hash for c in commits] == ["h1", "h2"]
    assert commits[1].spec_id == "ADR-003"
    assert commits[1].timestamp == 200
    assert commits[1].message == "feat(x): second [ADR-003]\n\nlonger body"
    assert commits[1].files == ["a.py", "b.py"]
    assert commits[0].spec_id is None


def test_parse_commit_without_files() -> None:
    commits = GitParser.parse(record("m1", 5, "Merge branch 'x'", []))
    assert commits[0].files == []
    assert commits[0].message == "Merge branch 'x'"


def test_build_command_full_vs_incremental() -> None:
    assert GitParser.build_command(None)[:3] == ["git", "log", "--all"]
    assert GitParser.build_command("abc")[:3] == ["git", "log", "abc..HEAD"]
    assert GitParser.build_command(None)[-1] == "--name-only"
    assert f"{RS}%H{US}%ct{US}%B" in GitParser.build_command(None)[3]


# ----------------------------------------------------------------- incremental


def test_first_run_is_full_then_incremental(tmp_path: Path, fake_git) -> None:
    git = fake_git(
        [
            record("h1", 100, "first", ["a.py", "b.py"]),
            record("h2", 200, "second [SPEC-7]", ["a.py"]),
        ]
    )
    idx, db = make_indexer(tmp_path)

    r1 = idx.index_history()
    assert r1.full_rebuild and r1.commits_ingested == 1
    assert "--all" in git.calls[0]

    r2 = idx.index_history()
    assert not r2.full_rebuild and r2.commits_ingested == 1
    assert "h1..HEAD" in git.calls[1]
    assert db.latest_commit_hash() == "h2"
    row = db.connection.execute("SELECT spec_id FROM commits WHERE hash='h2'").fetchone()
    assert row["spec_id"] == "SPEC-7"


def test_rerun_of_same_commit_is_idempotent(tmp_path: Path, fake_git) -> None:
    same = record("h1", 100, "first", ["a.py", "b.py"])
    fake_git([same, same])
    idx, db = make_indexer(tmp_path)
    idx.index_history()
    r2 = idx.index_history()
    assert r2.commits_already_indexed == 1
    assert db.connection.execute("SELECT total_changes FROM files WHERE path='a.py'").fetchone()[0] == 1
    assert db.connection.execute("SELECT co_commits FROM file_pairs").fetchone()[0] == 1


def test_latest_hash_tie_break_same_second(tmp_path: Path, fake_git) -> None:
    fake_git([record("h2", 100, "second", ["a.py"]) + record("h1", 100, "first", ["a.py"])])
    idx, db = make_indexer(tmp_path)
    idx.index_history()
    assert db.latest_commit_hash() == "h2"


# ----------------------------------------------------------------- self-healing


def test_self_healing_wipes_and_rebuilds_on_git_failure(tmp_path: Path, fake_git) -> None:
    git = fake_git(
        [
            record("old", 100, "old history", ["stale.py"]),
            subprocess.CalledProcessError(128, ["git"], stderr="fatal: bad revision"),
            record("new", 300, "rewritten", ["fresh.py"]),
        ]
    )
    idx, db = make_indexer(tmp_path)
    idx.index_history()
    assert db.latest_commit_hash() == "old"

    report = idx.index_history()
    assert report.full_rebuild
    assert "old..HEAD" in git.calls[1]
    assert "--all" in git.calls[2]
    paths = [r["path"] for r in db.connection.execute("SELECT path FROM files")]
    assert paths == ["fresh.py"]
    assert db.latest_commit_hash() == "new"


def test_full_run_failure_propagates(tmp_path: Path, fake_git) -> None:
    fake_git([subprocess.CalledProcessError(1, ["git"])])
    idx, _ = make_indexer(tmp_path)
    with pytest.raises(GitLogError):
        idx.index_history()


# ----------------------------------------------------------------- filtering


def test_graphignore_and_mass_refactor_filter(tmp_path: Path, fake_git) -> None:
    big = [f"gen/f{i}.py" for i in range(60)]
    fake_git(
        [
            record("mass", 100, "mass refactor", big)
            + record("ok", 50, "fine", ["a.py", "poetry.lock", "b.py"])
        ]
    )
    idx, db = make_indexer(tmp_path, patterns=["*.lock"])
    idx.mass_refactor_limit = 50
    report = idx.index_history()
    assert report.commits_skipped_mass_refactor == 1
    assert report.commits_ingested == 1
    paths = sorted(r["path"] for r in db.connection.execute("SELECT path FROM files"))
    assert paths == ["a.py", "b.py"]


def test_mass_refactor_limit_counts_after_ignore_filter(tmp_path: Path, fake_git) -> None:
    files = [f"src/f{i}.py" for i in range(50)] + ["x.lock", "y.lock"]
    fake_git([record("c", 1, "m", files)])
    idx, _ = make_indexer(tmp_path, patterns=["*.lock"])
    assert idx.index_history().commits_ingested == 1  # 50 after filtering, not > 50


# ----------------------------------------------------------------- jaccard


def test_jaccard_math_and_threshold(tmp_path: Path, fake_git) -> None:
    # a: 4 changes, b: 2 changes, c: 1 change; a&b co-commit twice, a&c once.
    fake_git(
        [
            record("h4", 4, "a alone", ["a.py"])
            + record("h3", 3, "a+c", ["a.py", "c.py"])
            + record("h2", 2, "a+b", ["a.py", "b.py"])
            + record("h1", 1, "a+b", ["b.py", "a.py"])
        ]
    )
    idx, db = make_indexer(tmp_path)
    idx.index_history()

    rows = db.query_coupled_files("a.py", 0.0)
    assert rows[0] == ("b.py", pytest.approx(2 / (4 + 2 - 2)))   # 0.5
    assert rows[1] == ("c.py", pytest.approx(1 / (4 + 1 - 1)))   # 0.25

    # Query from the other side of the pair works (file_b as target).
    assert db.query_coupled_files("b.py", 0.0) == [("a.py", pytest.approx(0.5))]
    # Threshold is inclusive.
    assert [p for p, _ in db.query_coupled_files("a.py", 0.5)] == ["b.py"]  # inclusive bound
    assert db.query_coupled_files("a.py", 0.9) == []


def test_pairs_are_canonically_ordered(tmp_path: Path, fake_git) -> None:
    fake_git([record("h1", 1, "m", ["z.py", "a.py"]) + record("h0", 0, "m", ["a.py", "z.py"])])
    idx, db = make_indexer(tmp_path)
    idx.index_history()
    pairs = db.connection.execute("SELECT file_a_id, file_b_id, co_commits FROM file_pairs").fetchall()
    assert len(pairs) == 1
    assert pairs[0]["file_a_id"] < pairs[0]["file_b_id"]
    assert pairs[0]["co_commits"] == 2
