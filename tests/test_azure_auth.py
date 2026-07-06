"""Auth method resolution + credential selection tests. No Azure calls."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

from fabric_drift_detective import azure_auth
from fabric_drift_detective.azure_auth import (
    FABRIC_SCOPE,
    NotebookUtilsCredential,
    get_credential,
    resolve_auth_method,
)


@pytest.fixture(autouse=True)
def _clear_cache_and_env(monkeypatch):
    """Each test starts with a cold credential cache and clean env."""
    get_credential.cache_clear()
    for var in ("FABRIC_AUTH_METHOD", "AZURE_TENANT_ID", "AZURE_CLIENT_ID",
                "AZURE_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    yield
    get_credential.cache_clear()


def _fake_notebookutils(token: str = "nb-token") -> ModuleType:
    mod = ModuleType("notebookutils")
    mod.credentials = SimpleNamespace(getToken=lambda audience: token)
    return mod


# ---------------------------------------------------------------- resolution
def test_default_is_client_secret_outside_fabric():
    assert resolve_auth_method() == "client_secret"


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("FABRIC_AUTH_METHOD", "managed_identity")
    assert resolve_auth_method() == "managed_identity"


def test_invalid_method_rejected(monkeypatch):
    monkeypatch.setenv("FABRIC_AUTH_METHOD", "hopes_and_dreams")
    with pytest.raises(ValueError, match="hopes_and_dreams"):
        resolve_auth_method()


def test_autodetects_notebookutils_when_importable(monkeypatch):
    monkeypatch.setitem(sys.modules, "notebookutils", _fake_notebookutils())
    assert resolve_auth_method() == "notebookutils"


# ---------------------------------------------------------------- credentials
def test_client_secret_missing_env_raises_helpfully():
    with pytest.raises(EnvironmentError, match="FABRIC_AUTH_METHOD"):
        get_credential()


def test_client_secret_credential_built(monkeypatch):
    monkeypatch.setenv("AZURE_TENANT_ID", "t")
    monkeypatch.setenv("AZURE_CLIENT_ID", "c")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "s")
    with patch("azure.identity.ClientSecretCredential") as mock_cred:
        get_credential()
    mock_cred.assert_called_once_with(
        tenant_id="t", client_id="c", client_secret="s"
    )


def test_managed_identity_credential_built(monkeypatch):
    monkeypatch.setenv("FABRIC_AUTH_METHOD", "managed_identity")
    with patch("azure.identity.ManagedIdentityCredential") as mock_cred:
        get_credential()
    mock_cred.assert_called_once_with()  # no client_id set -> system-assigned


def test_managed_identity_user_assigned(monkeypatch):
    monkeypatch.setenv("FABRIC_AUTH_METHOD", "managed_identity")
    monkeypatch.setenv("AZURE_CLIENT_ID", "uami-id")
    with patch("azure.identity.ManagedIdentityCredential") as mock_cred:
        get_credential()
    mock_cred.assert_called_once_with(client_id="uami-id")


def test_notebookutils_credential_selected(monkeypatch):
    monkeypatch.setenv("FABRIC_AUTH_METHOD", "notebookutils")
    assert isinstance(get_credential(), NotebookUtilsCredential)


def test_notebookutils_token_strips_default_suffix(monkeypatch):
    audiences: list[str] = []
    mod = ModuleType("notebookutils")
    mod.credentials = SimpleNamespace(
        getToken=lambda audience: audiences.append(audience) or "tok"
    )
    monkeypatch.setitem(sys.modules, "notebookutils", mod)
    token = NotebookUtilsCredential().get_token(FABRIC_SCOPE)
    assert token.token == "tok"
    assert audiences == ["https://api.fabric.microsoft.com"]


def test_get_token_uses_cached_credential(monkeypatch):
    monkeypatch.setenv("FABRIC_AUTH_METHOD", "notebookutils")
    monkeypatch.setitem(sys.modules, "notebookutils", _fake_notebookutils("abc"))
    assert azure_auth.get_token(FABRIC_SCOPE) == "abc"
