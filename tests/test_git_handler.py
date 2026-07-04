"""GitHandler security guards: traversal, symlinks, size caps, branch restore."""

from __future__ import annotations

import os
import subprocess

import pytest

from src.git_handler import GitHandler


@pytest.fixture
def repo(tmp_path):
    """Temp git repo with a reports dir and one TMDL file."""
    reports = tmp_path / "pbip_reports" / "definition" / "tables"
    reports.mkdir(parents=True)
    (reports / "Dim_Customer.tmdl").write_text(
        "table Dim_Customer\n\tcolumn Email\n\t\tsourceColumn: email\n",
        encoding="utf-8",
    )
    (tmp_path / "secret.txt").write_text("do not touch", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
        cwd=tmp_path, check=True,
    )
    return tmp_path


def _handler(repo, **kwargs) -> GitHandler:
    return GitHandler(repo_dir=repo, reports_dir="pbip_reports", **kwargs)


def test_apply_fix_happy_path(repo):
    applied = _handler(repo).apply_fixes([{
        "file": "definition/tables/Dim_Customer.tmdl",
        "find": "sourceColumn: email",
        "replace": "sourceColumn: email_address",
    }])
    assert len(applied) == 1
    text = (repo / "pbip_reports/definition/tables/Dim_Customer.tmdl").read_text()
    assert "email_address" in text


def test_traversal_path_rejected(repo):
    applied = _handler(repo).apply_fixes([{
        "file": "../secret.txt", "find": "do not touch", "replace": "owned",
    }])
    assert applied == []
    assert (repo / "secret.txt").read_text() == "do not touch"


def test_absolute_path_rejected(repo):
    target = repo / "secret.txt"
    applied = _handler(repo).apply_fixes([{
        "file": str(target), "find": "do not touch", "replace": "owned",
    }])
    assert applied == []
    assert target.read_text() == "do not touch"


def test_symlink_rejected(repo):
    link = repo / "pbip_reports" / "evil.tmdl"
    try:
        os.symlink(repo / "secret.txt", link)
    except OSError:
        pytest.skip("symlink creation not permitted on this system")
    applied = _handler(repo).apply_fixes([{
        "file": "evil.tmdl", "find": "do not touch", "replace": "owned",
    }])
    assert applied == []
    assert (repo / "secret.txt").read_text() == "do not touch"


def test_oversized_file_skipped(repo):
    big = repo / "pbip_reports" / "big.tmdl"
    big.write_text("x" * 100, encoding="utf-8")
    applied = _handler(repo, max_file_bytes=10).apply_fixes([{
        "file": "big.tmdl", "find": "x", "replace": "y",
    }])
    assert applied == []


def test_too_many_fixes_aborts(repo):
    fixes = [{"file": f"f{i}.tmdl", "find": "a", "replace": "b"} for i in range(3)]
    with pytest.raises(ValueError, match="cap is 2"):
        _handler(repo, max_fixes=2).apply_fixes(fixes)


def test_missing_find_string_skipped(repo):
    applied = _handler(repo).apply_fixes([{
        "file": "definition/tables/Dim_Customer.tmdl",
        "find": "not in the file", "replace": "x",
    }])
    assert applied == []


def test_dry_run_touches_nothing(repo):
    outcome = _handler(repo).create_pr(
        [{"file": "definition/tables/Dim_Customer.tmdl",
          "find": "sourceColumn: email", "replace": "changed"}],
        {"pr_title": "t", "pr_body": "b", "commit_subject": "s"},
        dry_run=True,
    )
    assert outcome.dry_run
    text = (repo / "pbip_reports/definition/tables/Dim_Customer.tmdl").read_text()
    assert "changed" not in text


def test_failed_pr_restores_original_branch(repo):
    handler = _handler(repo)
    # remote 'origin' does not exist -> push fails mid-flow
    outcome = handler.create_pr(
        [{"file": "definition/tables/Dim_Customer.tmdl",
          "find": "sourceColumn: email", "replace": "sourceColumn: e2"}],
        {"pr_title": "t", "pr_body": "b", "commit_subject": "fix: drift"},
        dry_run=False,
    )
    assert outcome.error is not None
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert branch == "main"


def test_no_applicable_fixes_returns_to_original_branch(repo):
    handler = _handler(repo)
    outcome = handler.create_pr(
        [{"file": "nope.tmdl", "find": "a", "replace": "b"}],
        {"pr_title": "t", "pr_body": "b", "commit_subject": "s"},
        dry_run=False,
    )
    assert outcome.error == "no fixes applied; PR skipped"
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert branch == "main"
