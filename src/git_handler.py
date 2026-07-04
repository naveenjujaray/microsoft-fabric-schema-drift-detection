"""Git integration: branch, apply TMDL fixes, open a PR.

Never commits to the base branch directly - all fixes land on a new
``drift-fix/...`` branch. PR creation prefers the ``gh`` CLI and falls
back to the GitHub REST API (GITHUB_TOKEN + GITHUB_REPO env vars).

``dry_run=True`` performs no git/network operations and just returns
what *would* happen - used by the simulate-mode demo.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)


@dataclass
class PROutcome:
    """Result of (attempted) PR creation."""

    branch: str
    commit_subject: str
    pr_title: str
    pr_body: str
    pr_url: str | None = None
    applied_files: list[str] = field(default_factory=list)
    dry_run: bool = False
    error: str | None = None


class GitHandler:
    """Applies fixes on a branch and opens a PR."""

    def __init__(
        self,
        repo_dir: str | Path = ".",
        reports_dir: str = "pbip_reports",
        remote: str = "origin",
        base_branch: str = "main",
        branch_prefix: str = "drift-fix/",
        use_gh_cli: bool = True,
    ) -> None:
        self.repo_dir = Path(repo_dir)
        self.reports_dir = self.repo_dir / reports_dir
        self.remote = remote
        self.base_branch = base_branch
        self.branch_prefix = branch_prefix
        self.use_gh_cli = use_gh_cli

    # ------------------------------------------------------------------
    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def apply_fixes(self, fixes: list[dict[str, Any]]) -> list[str]:
        """Apply find/replace TMDL edits under the reports dir.

        Each fix: {"file", "find", "replace", "description"}. Files are
        resolved relative to the reports dir; missing files or
        non-matching "find" strings are skipped with a warning (never
        corrupt what we do not understand).
        """
        applied: list[str] = []
        for fix in fixes:
            rel = fix.get("file", "")
            path = self.reports_dir / rel
            if not path.exists():
                logger.warning("fix target missing, skipped: %s", path)
                continue
            text = path.read_text(encoding="utf-8")
            find = fix.get("find", "")
            if not find or find not in text:
                logger.warning("find-string absent, skipped: %s in %s", find, rel)
                continue
            path.write_text(
                text.replace(find, fix.get("replace", "")), encoding="utf-8"
            )
            applied.append(str(path.relative_to(self.repo_dir)))
            logger.info("applied fix to %s: %s", rel, fix.get("description", ""))
        return applied

    # ------------------------------------------------------------------
    def create_pr(
        self,
        fixes: list[dict[str, Any]],
        pr_content: dict[str, Any],
        dry_run: bool = False,
    ) -> PROutcome:
        """Branch, commit applied fixes, push, open PR."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        branch = f"{self.branch_prefix}{stamp}"
        outcome = PROutcome(
            branch=branch,
            commit_subject=pr_content.get("commit_subject", "fix: schema drift"),
            pr_title=pr_content.get("pr_title", "Schema drift fixes"),
            pr_body=pr_content.get("pr_body", ""),
            dry_run=dry_run,
        )
        if dry_run:
            outcome.applied_files = [
                f.get("file", "?") for f in fixes
            ]  # what would be touched
            return outcome

        try:
            self._git("checkout", "-b", branch, self.base_branch)
            outcome.applied_files = self.apply_fixes(fixes)
            if not outcome.applied_files:
                outcome.error = "no fixes applied; PR skipped"
                self._git("checkout", self.base_branch)
                return outcome
            self._git("add", *outcome.applied_files)
            message = outcome.commit_subject
            body = pr_content.get("commit_body", "")
            if body:
                message += f"\n\n{body}"
            self._git("commit", "-m", message)
            self._git("push", "-u", self.remote, branch)
            outcome.pr_url = self._open_pr(branch, outcome)
        except subprocess.CalledProcessError as exc:
            outcome.error = f"git failed: {exc.stderr or exc}"
            logger.error(outcome.error)
        return outcome

    # ------------------------------------------------------------------
    def _open_pr(self, branch: str, outcome: PROutcome) -> str | None:
        if self.use_gh_cli and shutil.which("gh"):
            out = subprocess.run(
                [
                    "gh", "pr", "create",
                    "--base", self.base_branch,
                    "--head", branch,
                    "--title", outcome.pr_title,
                    "--body", outcome.pr_body,
                ],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                check=True,
            )
            return out.stdout.strip() or None
        return self._open_pr_rest(branch, outcome)

    def _open_pr_rest(self, branch: str, outcome: PROutcome) -> str | None:
        """GitHub REST fallback: POST /repos/{owner}/{repo}/pulls."""
        token = os.environ.get("GITHUB_TOKEN")
        repo = os.environ.get("GITHUB_REPO")  # "owner/name"
        if not token or not repo:
            logger.warning("GITHUB_TOKEN/GITHUB_REPO unset; cannot open PR via REST")
            return None
        resp = requests.post(
            f"https://api.github.com/repos/{repo}/pulls",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            json={
                "title": outcome.pr_title,
                "body": outcome.pr_body,
                "head": branch,
                "base": self.base_branch,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("html_url")
