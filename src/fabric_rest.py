"""Fabric REST API client: items, semantic-model TMDL, lakehouse tables.

Used where the CLI can't return schema metadata. Auth is the shared
``ClientSecretCredential`` from ``azure_auth`` (same app registration
as Teams/Outlook Graph — one auth stack).

Endpoints (Fabric REST v1, see learn.microsoft.com/rest/api/fabric):
    GET  /workspaces/{ws}/items
    GET  /workspaces/{ws}/lakehouses/{id}/tables
    POST /workspaces/{ws}/semanticModels/{id}/getDefinition  (TMDL parts)
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

import requests

from .azure_auth import FABRIC_SCOPE, get_token

logger = logging.getLogger(__name__)


class FabricRestError(RuntimeError):
    """A Fabric REST call failed."""


class FabricRest:
    """Minimal Fabric REST client for schema inspection."""

    def __init__(
        self,
        api_base: str = "https://api.fabric.microsoft.com/v1",
        timeout: int = 60,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {get_token(FABRIC_SCOPE)}"}

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = f"{self.api_base}/{path.lstrip('/')}"
        resp = requests.request(
            method, url, headers=self._headers(), timeout=self.timeout, **kwargs
        )
        if resp.status_code == 429:  # throttled: honor Retry-After once
            wait = int(resp.headers.get("Retry-After", "10"))
            logger.warning("Fabric throttled; retrying in %ss", wait)
            time.sleep(wait)
            resp = requests.request(
                method, url, headers=self._headers(), timeout=self.timeout, **kwargs
            )
        if resp.status_code >= 400:
            raise FabricRestError(
                f"{method} {path} -> {resp.status_code}: {resp.text[:500]}"
            )
        return resp

    # ------------------------------------------------------------------
    def list_items(self, workspace_id: str) -> list[dict[str, Any]]:
        resp = self._request("GET", f"workspaces/{workspace_id}/items")
        return resp.json().get("value", [])

    def list_lakehouse_tables(
        self, workspace_id: str, lakehouse_id: str
    ) -> list[dict[str, Any]]:
        resp = self._request(
            "GET", f"workspaces/{workspace_id}/lakehouses/{lakehouse_id}/tables"
        )
        return resp.json().get("data", resp.json().get("value", []))

    # ------------------------------------------------------------------
    def get_semantic_model_tmdl(
        self, workspace_id: str, model_id: str
    ) -> dict[str, str]:
        """Returns {part_path: decoded_tmdl_text} for the model definition.

        getDefinition is a long-running operation: 202 + Location header
        polling, then the result payload carries base64-encoded parts.
        """
        url = (
            f"workspaces/{workspace_id}/semanticModels/{model_id}/"
            "getDefinition?format=TMDL"
        )
        resp = self._request("POST", url)
        payload = resp.json() if resp.status_code == 200 else self._poll_lro(resp)
        parts: dict[str, str] = {}
        for part in payload.get("definition", {}).get("parts", []):
            if part.get("payloadType") == "InlineBase64":
                parts[part["path"]] = base64.b64decode(part["payload"]).decode(
                    "utf-8", errors="replace"
                )
        return parts

    def _poll_lro(self, resp: requests.Response, max_wait: int = 300) -> dict[str, Any]:
        """Poll a 202 long-running operation until success."""
        location = resp.headers.get("Location", "")
        deadline = time.monotonic() + max_wait
        while location and time.monotonic() < deadline:
            time.sleep(int(resp.headers.get("Retry-After", "5")))
            poll = requests.get(
                location, headers=self._headers(), timeout=self.timeout
            )
            if poll.status_code == 200:
                body = poll.json()
                if body.get("status") in ("Succeeded", None):
                    # result may be at a /result suffix
                    result = requests.get(
                        f"{location.rstrip('/')}/result",
                        headers=self._headers(),
                        timeout=self.timeout,
                    )
                    return result.json() if result.ok else body
                if body.get("status") == "Failed":
                    raise FabricRestError(f"LRO failed: {body}")
            resp = poll
        raise FabricRestError("LRO polling timed out")

    # ------------------------------------------------------------------
    def query_sql_endpoint(
        self, sql_endpoint: str, database: str, query: str
    ) -> list[tuple]:
        """Query the lakehouse/warehouse SQL analytics endpoint via ODBC.

        Requires ``pyodbc`` + 'ODBC Driver 18 for SQL Server' installed.
        Kept optional so simulate mode never needs it.
        """
        import pyodbc  # deferred: optional dependency for live mode

        conn_str = (
            "Driver={ODBC Driver 18 for SQL Server};"
            f"Server={sql_endpoint};Database={database};"
            "Authentication=ActiveDirectoryServicePrincipal;"
        )
        import os

        conn_str += (
            f"UID={os.environ.get('AZURE_CLIENT_ID', '')};"
            f"PWD={os.environ.get('AZURE_CLIENT_SECRET', '')};"
        )
        with pyodbc.connect(conn_str, timeout=self.timeout) as conn:
            cur = conn.cursor()
            cur.execute(query)
            return cur.fetchall()
