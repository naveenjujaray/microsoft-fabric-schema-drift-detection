"""Fabric REST API client: items, semantic-model TMDL, lakehouse tables.

Used where the CLI can't return schema metadata. Auth is the shared
``ClientSecretCredential`` from ``azure_auth`` (same app registration
as Teams/Outlook Graph — one auth stack).

Resiliency: every request runs through a retry loop with exponential
backoff + jitter. Throttling (429) honors ``Retry-After``; transient
server errors (5xx/408) and connection/timeout failures are retried;
4xx client errors fail fast.

Endpoints (Fabric REST v1, see learn.microsoft.com/rest/api/fabric):
    GET  /workspaces/{ws}/items
    GET  /workspaces/{ws}/lakehouses/{id}/tables
    POST /workspaces/{ws}/semanticModels/{id}/getDefinition  (TMDL parts)
"""

from __future__ import annotations

import base64
import logging
import random
import time
from typing import Any

import requests

from .azure_auth import FABRIC_SCOPE, get_token

logger = logging.getLogger(__name__)

# HTTP statuses worth retrying: throttle, request timeout, server errors
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
_MAX_BACKOFF_SECONDS = 60.0


class FabricRestError(RuntimeError):
    """A Fabric REST call failed."""


def _retry_after_seconds(resp: requests.Response, fallback: float) -> float:
    """Parse Retry-After (seconds form); clamp to the backoff ceiling."""
    raw = resp.headers.get("Retry-After", "")
    try:
        wait = float(raw)
    except (TypeError, ValueError):
        wait = fallback
    return max(0.0, min(wait, _MAX_BACKOFF_SECONDS))


class FabricRest:
    """Minimal, resilient Fabric REST client for schema inspection."""

    def __init__(
        self,
        api_base: str = "https://api.fabric.microsoft.com/v1",
        timeout: int = 60,
        max_retries: int = 4,
        backoff_base: float = 2.0,
        session: requests.Session | None = None,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.backoff_base = backoff_base
        self.session = session or requests.Session()

    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {get_token(FABRIC_SCOPE)}"}

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with full jitter: base^attempt capped."""
        ceiling = min(self.backoff_base**attempt, _MAX_BACKOFF_SECONDS)
        return random.uniform(0, ceiling)

    def _send(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """One request with retries for transient failures.

        Retries on 429/408/5xx and on connection/timeout errors, with
        exponential backoff (Retry-After wins when the server sends it).
        Raises ``FabricRestError`` on non-retryable errors or when the
        retry budget is exhausted.
        """
        last_error: str = ""
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.request(
                    method, url, headers=self._headers(),
                    timeout=self.timeout, **kwargs,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = f"transient network error: {exc}"
                if attempt >= self.max_retries:
                    break
                wait = self._backoff(attempt + 1)
                logger.warning(
                    "Fabric request failed (%s); retry %d/%d in %.1fs",
                    exc.__class__.__name__, attempt + 1, self.max_retries, wait,
                )
                time.sleep(wait)
                continue

            if resp.status_code in _RETRYABLE_STATUS:
                last_error = f"{resp.status_code}: {resp.text[:200]}"
                if attempt >= self.max_retries:
                    break
                wait = _retry_after_seconds(resp, self._backoff(attempt + 1))
                logger.warning(
                    "Fabric returned %d; retry %d/%d in %.1fs",
                    resp.status_code, attempt + 1, self.max_retries, wait,
                )
                time.sleep(wait)
                continue

            if resp.status_code >= 400:
                raise FabricRestError(
                    f"{method} {url} -> {resp.status_code}: {resp.text[:500]}"
                )
            return resp

        raise FabricRestError(
            f"{method} {url} failed after {self.max_retries + 1} attempt(s): "
            f"{last_error}"
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = f"{self.api_base}/{path.lstrip('/')}"
        return self._send(method, url, **kwargs)

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
        body = resp.json()
        return body.get("data", body.get("value", []))

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
        """Poll a 202 long-running operation until success or timeout."""
        location = resp.headers.get("Location", "")
        if not location:
            raise FabricRestError("LRO response missing Location header")
        deadline = time.monotonic() + max_wait
        wait = _retry_after_seconds(resp, 5.0)
        while time.monotonic() < deadline:
            time.sleep(wait)
            poll = self._send("GET", location)
            wait = _retry_after_seconds(poll, 5.0)
            body = poll.json()
            status = body.get("status")
            if status == "Failed":
                raise FabricRestError(f"LRO failed: {body}")
            if status in ("Succeeded", None):
                # result may live at a /result suffix
                try:
                    result = self._send("GET", f"{location.rstrip('/')}/result")
                    return result.json()
                except FabricRestError:
                    return body
        raise FabricRestError(f"LRO polling timed out after {max_wait}s")

    # ------------------------------------------------------------------
    def query_sql_endpoint(
        self, sql_endpoint: str, database: str, query: str,
        params: tuple[Any, ...] = (),
    ) -> list[tuple]:
        """Query the lakehouse/warehouse SQL analytics endpoint via ODBC.

        Requires ``pyodbc`` + 'ODBC Driver 18 for SQL Server' installed.
        Kept optional so simulate mode never needs it. ``params`` are
        passed through as ODBC bind parameters - never interpolate
        untrusted values into ``query``.
        """
        import os

        import pyodbc  # deferred: optional dependency for live mode

        conn_str = (
            "Driver={ODBC Driver 18 for SQL Server};"
            f"Server={sql_endpoint};Database={database};"
            "Authentication=ActiveDirectoryServicePrincipal;"
            f"UID={os.environ.get('AZURE_CLIENT_ID', '')};"
            f"PWD={os.environ.get('AZURE_CLIENT_SECRET', '')};"
        )
        with pyodbc.connect(conn_str, timeout=self.timeout) as conn:
            cur = conn.cursor()
            cur.execute(query, params) if params else cur.execute(query)
            return cur.fetchall()
