"""Thin, testable wrapper around the Microsoft Fabric CLI (``fab``).

Install: ``pip install ms-fabric-cli``.

Command syntax verified against Microsoft Learn
(learn.microsoft.com/rest/api/fabric/articles/fabric-command-line-interface
and learn.microsoft.com/fabric/database/sql/deploy-cli):

    fab auth login                          # interactive / SPN prompts
    fab create <ws>.Workspace/<item>.<Type> # e.g. Bronze.Lakehouse
    fab ls <ws>.Workspace                   # list items
    fab exists <path>
    fab get <path> [-q <jmespath>]
    fab api <rest-path> -X <method> -i <json-body>   # raw REST escape hatch

Service-principal login flags (-u/-p/--tenant) follow the official
fabric-cli reference (microsoft.github.io/fabric-cli); if your CLI
version differs, run ``fab auth login`` interactively or use
``fabric_rest.py`` which needs no CLI at all.

Every call goes through ``run()`` so tests can mock a single choke
point.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class FabricCLIError(RuntimeError):
    """A ``fab`` invocation failed."""


@dataclass
class FabResult:
    """Captured output of one ``fab`` invocation."""

    args: list[str]
    stdout: str
    stderr: str
    returncode: int

    def json(self) -> Any:
        """Parse stdout as JSON (raises on garbage)."""
        return json.loads(self.stdout)


class FabricCLI:
    """All ``fab`` interactions, mockable via ``run``."""

    def __init__(self, executable: str = "fab", timeout: int = 120) -> None:
        self.executable = executable
        self.timeout = timeout

    # ------------------------------------------------------------------
    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    def run(self, *args: str, check: bool = True) -> FabResult:
        """Execute ``fab <args>`` and capture output."""
        cmd = [self.executable, *args]
        logger.debug("running: %s", " ".join(cmd))
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=self.timeout
        )
        result = FabResult(
            args=list(args),
            stdout=proc.stdout.strip(),
            stderr=proc.stderr.strip(),
            returncode=proc.returncode,
        )
        if check and proc.returncode != 0:
            raise FabricCLIError(
                f"fab {' '.join(args)} failed ({proc.returncode}): {result.stderr}"
            )
        return result

    # ------------------------------------------------------------------
    def login_service_principal(
        self, client_id: str, client_secret: str, tenant_id: str
    ) -> FabResult:
        """SPN login (flags per official fabric-cli reference)."""
        return self.run(
            "auth", "login", "-u", client_id, "-p", client_secret,
            "--tenant", tenant_id,
        )

    def create_item(self, workspace: str, name: str, item_type: str,
                    params: str | None = None) -> FabResult:
        """``fab create ws.Workspace/name.Type`` (Lakehouse, Warehouse...)."""
        path = f"{workspace}.Workspace/{name}.{item_type}"
        args = ["create", path]
        if params:
            args += ["-P", params]
        return self.run(*args)

    def list_items(self, workspace: str) -> FabResult:
        return self.run("ls", f"{workspace}.Workspace")

    def exists(self, path: str) -> bool:
        result = self.run("exists", path, check=False)
        return result.returncode == 0 and "true" in result.stdout.lower()

    def get(self, path: str, query: str | None = None) -> FabResult:
        args = ["get", path]
        if query:
            args += ["-q", query]
        return self.run(*args)

    def api(self, rest_path: str, method: str = "get",
            body: dict[str, Any] | None = None) -> FabResult:
        """Raw Fabric REST call through the CLI (``fab api``)."""
        args = ["api", rest_path, "-X", method]
        if body is not None:
            args += ["-i", json.dumps(body)]
        return self.run(*args)
