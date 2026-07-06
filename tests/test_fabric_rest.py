"""Fabric REST client resiliency: retries, backoff, transient detection."""

from __future__ import annotations

from typing import Any

import pytest
import requests

from fabric_drift_detective.fabric_rest import (
    FabricRest,
    FabricRestError,
    _retry_after_seconds,
)


class FakeResponse:
    def __init__(self, status_code: int, body: Any = None,
                 headers: dict[str, str] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = text

    def json(self) -> Any:
        return self._body


class FakeSession:
    """Scripted session: pops one canned outcome per request."""

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def _no_sleep_no_token(monkeypatch):
    monkeypatch.setattr("fabric_drift_detective.fabric_rest.time.sleep", lambda s: None)
    monkeypatch.setattr("fabric_drift_detective.fabric_rest.get_token", lambda scope: "fake-token")


def _client(session: FakeSession, retries: int = 3) -> FabricRest:
    return FabricRest(max_retries=retries, session=session)  # type: ignore[arg-type]


def test_success_first_try():
    session = FakeSession([FakeResponse(200, {"value": [{"id": "1"}]})])
    assert _client(session).list_items("ws") == [{"id": "1"}]
    assert len(session.calls) == 1


def test_retries_on_429_then_succeeds():
    session = FakeSession([
        FakeResponse(429, headers={"Retry-After": "0"}),
        FakeResponse(429, headers={"Retry-After": "0"}),
        FakeResponse(200, {"value": []}),
    ])
    assert _client(session).list_items("ws") == []
    assert len(session.calls) == 3


def test_retries_on_5xx():
    session = FakeSession([
        FakeResponse(503),
        FakeResponse(200, {"value": []}),
    ])
    assert _client(session).list_items("ws") == []


def test_retries_on_connection_error():
    session = FakeSession([
        requests.ConnectionError("reset by peer"),
        requests.Timeout("read timed out"),
        FakeResponse(200, {"value": []}),
    ])
    assert _client(session).list_items("ws") == []


def test_gives_up_after_budget_exhausted():
    session = FakeSession([FakeResponse(503)] * 4)
    with pytest.raises(FabricRestError, match="after 4 attempt"):
        _client(session, retries=3).list_items("ws")
    assert len(session.calls) == 4


def test_client_errors_fail_fast_no_retry():
    session = FakeSession([FakeResponse(404, text="not found")])
    with pytest.raises(FabricRestError, match="404"):
        _client(session).list_items("ws")
    assert len(session.calls) == 1


def test_retry_after_parsing_clamps():
    assert _retry_after_seconds(FakeResponse(429, headers={"Retry-After": "7"}), 1.0) == 7.0
    assert _retry_after_seconds(FakeResponse(429, headers={"Retry-After": "9999"}), 1.0) == 60.0
    assert _retry_after_seconds(FakeResponse(429, headers={"Retry-After": "bogus"}), 1.5) == 1.5
    assert _retry_after_seconds(FakeResponse(429), 2.0) == 2.0


def test_lro_polls_until_succeeded():
    import base64

    part = {
        "path": "definition/tables/T.tmdl",
        "payloadType": "InlineBase64",
        "payload": base64.b64encode(b"table T").decode(),
    }
    session = FakeSession([
        FakeResponse(202, headers={"Location": "https://api/lro/1", "Retry-After": "0"}),
        FakeResponse(200, {"status": "Running"}, headers={"Retry-After": "0"}),
        FakeResponse(200, {"status": "Succeeded"}, headers={"Retry-After": "0"}),
        FakeResponse(200, {"definition": {"parts": [part]}}),
    ])
    parts = _client(session).get_semantic_model_tmdl("ws", "model")
    assert parts == {"definition/tables/T.tmdl": "table T"}


def test_lro_failed_status_raises():
    session = FakeSession([
        FakeResponse(202, headers={"Location": "https://api/lro/1", "Retry-After": "0"}),
        FakeResponse(200, {"status": "Failed", "error": "boom"}, headers={"Retry-After": "0"}),
    ])
    with pytest.raises(FabricRestError, match="LRO failed"):
        _client(session).get_semantic_model_tmdl("ws", "model")


def test_lro_missing_location_raises():
    session = FakeSession([FakeResponse(202, headers={})])
    with pytest.raises(FabricRestError, match="Location"):
        _client(session).get_semantic_model_tmdl("ws", "model")
