"""LLM reasoner tests - Anthropic SDK fully mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fabric_drift_detective.backends.base import Layer
from fabric_drift_detective.llm_reasoner import (
    ClaudeReasoner,
    MockReasoner,
    make_reasoner,
    parse_llm_json,
)
from fabric_drift_detective.schema_diff import DriftRecord, DriftType, Severity


def _drift(**kw) -> DriftRecord:
    defaults = dict(
        layer=Layer.SILVER,
        drift_type=DriftType.COLUMN_RENAME,
        severity=Severity.CRITICAL,
        table="customers",
        column="email",
        old="email",
        new="email_address",
        auto_fixable=True,
        downstream_impact=["reports:Customer Detail.Customer.Email"],
    )
    defaults.update(kw)
    return DriftRecord(**defaults)


# ---------------------------------------------------------------- parsing
def test_parse_plain_json():
    assert parse_llm_json('{"a": 1}') == {"a": 1}


def test_parse_fenced_json():
    assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_with_prose():
    text = 'Here you go:\n{"analyses": []}\nHope that helps!'
    assert parse_llm_json(text) == {"analyses": []}


def test_parse_garbage_returns_empty():
    assert parse_llm_json("not json at all") == {}


# ---------------------------------------------------------------- mock reasoner
def test_mock_impact_analysis_shape():
    result = MockReasoner().analyze_impact([_drift()])
    assert result["analyses"][0]["fixable"] == "yes"
    assert result["analyses"][0]["affected_reports"] == ["Customer Detail"]
    assert "summary" in result


def test_mock_fix_suggestion_only_renames():
    drifts = [
        _drift(),
        _drift(drift_type=DriftType.COLUMN_DROP, auto_fixable=False),
    ]
    fixes = MockReasoner().suggest_fixes(drifts, "")["fixes"]
    assert len(fixes) == 1
    assert fixes[0]["find"] == "sourceColumn: email"
    assert fixes[0]["replace"] == "sourceColumn: email_address"


def test_mock_pr_content_subject_length():
    content = MockReasoner().write_pr_content([_drift()], "summary here")
    assert len(content["commit_subject"]) <= 72
    assert "## Drift detected" in content["pr_body"]
    assert "## Needs human review" in content["pr_body"]


# ---------------------------------------------------------------- claude (mocked)
def _mock_anthropic_response(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    message = MagicMock()
    message.content = [block]
    return message


@patch("anthropic.Anthropic")
def test_claude_reasoner_parses_response(mock_cls):
    client = mock_cls.return_value
    client.messages.create.return_value = _mock_anthropic_response(
        '```json\n{"analyses": [{"drift_index": 0, "severity": "critical"}],'
        ' "summary": "bad day"}\n```'
    )
    reasoner = ClaudeReasoner(model="claude-test", api_key="k")
    result = reasoner.analyze_impact([_drift()])
    assert result["summary"] == "bad day"
    # model passed through from config
    assert client.messages.create.call_args.kwargs["model"] == "claude-test"


@patch("anthropic.Anthropic")
def test_claude_no_fixable_short_circuits(mock_cls):
    reasoner = ClaudeReasoner(api_key="k")
    result = reasoner.suggest_fixes(
        [_drift(auto_fixable=False)], "tmdl"
    )
    assert result == {"fixes": []}
    mock_cls.return_value.messages.create.assert_not_called()


def test_factory_without_key_returns_mock(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(make_reasoner({"enabled": True}), MockReasoner)


def test_factory_disabled_returns_mock(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert isinstance(make_reasoner({"enabled": False}), MockReasoner)
