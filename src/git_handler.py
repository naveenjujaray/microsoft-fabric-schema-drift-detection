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

# safety limits for LLM-proposed fixes (see GitHandler.apply_fixes)
DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024  # PBIP/TMDL sources are small text
DEFAULT_MAX_FIXES = 50  # a drift PR touching more files needs a human
_DENIED_PARTS = {".git", ".env"}


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
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_fixes: int = DEFAULT_MAX_FIXES,
    ) -> None:
        self.repo_dir = Path(repo_dir)
        self.reports_dir = self.repo_dir / reports_dir
        self.remote = remote
        self.base_branch = base_branch
        self.branch_prefix = branch_prefix
        self.use_gh_cli = use_gh_cli
        self.max_file_bytes = max_file_bytes
        self.max_fixes = max_fixes

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

    def _current_branch(self) -> str:
        return self._git("rev-parse", "--abbrev-ref", "HEAD")

    def _safe_fix_path(self, rel: str) -> Path | None:
        """Resolve an LLM-proposed fix path safely inside the reports dir.

        Fix paths come from model output, so they are untrusted input:
        reject traversal outside the reports dir, symlinks (writing
        through one would modify a file elsewhere), denied components
        (.git/.env) and absolute paths. Returns None when rejected.
        """
        if not rel:
            return None
        candidate = Path(rel)
        if candidate.is_absolute():
            logger.warning("fix path is absolute, rejected: %s", rel)
            return None
        root = self.reports_dir.resolve()
        path = (root / candidate).resolve()
        if not path.is_relative_to(root):
            logger.warning("fix path escapes reports dir, rejected: %s", rel)
            return None
        if any(p.lower() in _DENIED_PARTS for p in path.parts):
            logger.warning("fix path touches denied component, rejected: %s", rel)
            return None
        # reject a symlink at any level between root and the target
        probe = root
        for part in path.relative_to(root).parts:
            probe = probe / part
            if probe.is_symlink():
                logger.warning("fix path contains a symlink, rejected: %s", rel)
                return None
        return path

    def apply_fixes(self, fixes: list[dict[str, Any]]) -> list[str]:
        """Apply find/replace TMDL edits under the reports dir.

        Each fix: {"file", "find", "replace", "description"}. Files are
        resolved relative to the reports dir; missing files or
        non-matching "find" strings are skipped with a warning (never
        corrupt what we do not understand). Untrusted paths are guarded
        by ``_safe_fix_path``; files above ``max_file_bytes`` are
        skipped; more than ``max_fixes`` fixes aborts entirely (a large
        PBIP rewrite should be reviewed by a human, not automated).
        """
        if len(fixes) > self.max_fixes:
            raise ValueError(
                f"{len(fixes)} fixes proposed, cap is {self.max_fixes}; "
                "refusing bulk rewrite - apply manually after review"
            )
        applied: list[str] = []
        for fix in fixes:
            rel = fix.get("file", "")
            path = self._safe_fix_path(rel)
            if path is None:
                continue
            if not path.exists():
                logger.warning("fix target missing, skipped: %s", path)
                continue
            if not path.is_file():
                logger.warning("fix target not a regular file, skipped: %s", rel)
                continue
            if path.stat().st_size > self.max_file_bytes:
                logger.warning(
                    "fix target exceeds %d bytes, skipped: %s",
                    self.max_file_bytes, rel,
                )
                continue
            text = path.read_text(encoding="utf-8")
            find = fix.get("find", "")
            if not find or find not in text:
                logger.warning("find-string absent, skipped: %s in %s", find, rel)
                continue
            path.write_text(
                text.replace(find, fix.get("replace", "")), encoding="utf-8"
            )
            applied.append(str(path.relative_to(self.repo_dir.resolve())))
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

        original_branch = ""
        try:
            original_branch = self._current_branch()
            self._git("checkout", "-b", branch, self.base_branch)
            outcome.applied_files = self.apply_fixes(fixes)
            if not outcome.applied_files:
                outcome.error = "no fixes applied; PR skipped"
                self._git("checkout", original_branch)
                return outcome
            self._git("add", *outcome.applied_files)
            message = outcome.commit_subject
            body = pr_content.get("commit_body", "")
            if body:
                message += f"\n\n{body}"
            self._git("commit", "-m", message)
            self._git("push", "-u", self.remote, branch)
            outcome.pr_url = self._open_pr(branch, outcome)
            self._git("checkout", original_branch)
        except (subprocess.CalledProcessError, ValueError, OSError) as exc:
            stderr = getattr(exc, "stderr", "") or ""
            outcome.error = f"git failed: {stderr.strip() or exc}"
            logger.error(outcome.error)
            self._restore_branch(original_branch)
        return outcome

    def _restore_branch(self, original_branch: str) -> None:
        """Best-effort return to the branch we started on after a failure."""
        if not original_branch or original_branch == "HEAD":
            return
        try:
            self._git("checkout", original_branch)
        except subprocess.CalledProcessError as exc:
            logger.error(
                "could not restore branch %r: %s", original_branch,
                (exc.stderr or "").strip(),
            )

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
