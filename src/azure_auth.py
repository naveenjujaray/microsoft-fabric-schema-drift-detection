"""Single Azure auth stack shared by Fabric REST and Microsoft Graph.

One credential, four ways to obtain it — selected via the
``FABRIC_AUTH_METHOD`` env var (or ``fabric.auth_method`` in
config.yaml, which main.py propagates):

    client_secret     ClientSecretCredential from AZURE_TENANT_ID /
                      AZURE_CLIENT_ID / AZURE_CLIENT_SECRET (default
                      outside Fabric — unchanged behavior)
    managed_identity  ManagedIdentityCredential (Azure-hosted compute)
    notebookutils     Fabric notebook runtime token via
                      ``notebookutils.credentials.getToken`` — the
                      native path when running INSIDE a Fabric notebook
    default           DefaultAzureCredential (full fallback chain)

If ``FABRIC_AUTH_METHOD`` is unset, we auto-detect: use the Fabric
notebook runtime when ``notebookutils`` is importable, else fall back
to ``client_secret`` (the original behavior).

Scopes:
  * Fabric REST      : https://api.fabric.microsoft.com/.default
  * Microsoft Graph  : https://graph.microsoft.com/.default
    (Teams ChannelMessage.Send, Outlook Mail.Send application perms)
"""

from __future__ import annotations

import os
import time
from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from azure.core.credentials import AccessToken

FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

VALID_METHODS = ("client_secret", "managed_identity", "notebookutils", "default")


def _notebookutils_available() -> bool:
    """True when running inside a Fabric/Synapse notebook runtime."""
    try:
        import notebookutils  # noqa: F401  (only exists inside Fabric)

        return True
    except ImportError:
        return False


class NotebookUtilsCredential:
    """azure.core-compatible credential backed by the Fabric notebook
    runtime (``notebookutils.credentials.getToken``).

    Uses the notebook's executing identity — no secrets, no .env.
    Duck-types ``get_token`` so it drops into the same code paths as
    ``ClientSecretCredential``.
    """

    _TOKEN_TTL_GUESS = 3600  # getToken returns no expiry; assume 1h

    def get_token(self, *scopes: str, **_: Any) -> AccessToken:
        import notebookutils
        from azure.core.credentials import AccessToken

        # notebookutils expects an audience, not a ".default" scope
        audience = scopes[0].removesuffix("/.default") if scopes else FABRIC_SCOPE
        token = notebookutils.credentials.getToken(audience)
        return AccessToken(token, int(time.time()) + self._TOKEN_TTL_GUESS)


def resolve_auth_method() -> str:
    """Effective auth method: env override, else auto-detect."""
    method = os.environ.get("FABRIC_AUTH_METHOD", "").strip().lower()
    if method:
        if method not in VALID_METHODS:
            raise ValueError(
                f"FABRIC_AUTH_METHOD={method!r} invalid; "
                f"expected one of {VALID_METHODS}"
            )
        return method
    return "notebookutils" if _notebookutils_available() else "client_secret"


def _client_secret_credential():
    from azure.identity import ClientSecretCredential

    tenant = os.environ.get("AZURE_TENANT_ID", "")
    client = os.environ.get("AZURE_CLIENT_ID", "")
    secret = os.environ.get("AZURE_CLIENT_SECRET", "")
    if not (tenant and client and secret):
        raise OSError(
            "AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET must be "
            "set (see .env.example) for live Fabric or Graph access — or set "
            "FABRIC_AUTH_METHOD=managed_identity|notebookutils|default when "
            "running on Azure/Fabric compute."
        )
    return ClientSecretCredential(
        tenant_id=tenant, client_id=client, client_secret=secret
    )


@lru_cache(maxsize=1)
def get_credential():
    """The one shared credential, built for the resolved auth method."""
    method = resolve_auth_method()
    if method == "notebookutils":
        return NotebookUtilsCredential()
    if method == "managed_identity":
        from azure.identity import ManagedIdentityCredential

        client_id = os.environ.get("AZURE_CLIENT_ID", "")
        return (
            ManagedIdentityCredential(client_id=client_id)
            if client_id
            else ManagedIdentityCredential()
        )
    if method == "default":
        from azure.identity import DefaultAzureCredential

        return DefaultAzureCredential()
    return _client_secret_credential()


def get_token(scope: str) -> str:
    """Bearer token for the given scope."""
    return str(get_credential().get_token(scope).token)
