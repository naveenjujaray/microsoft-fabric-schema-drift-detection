"""Notification tests: payload shapes + failure isolation. No network."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fabric_drift_detective.backends.base import Layer
from fabric_drift_detective.notifications.base import DriftAlert
from fabric_drift_detective.notifications.console_channel import ConsoleChannel
from fabric_drift_detective.notifications.dispatcher import Dispatcher, build_dispatcher
from fabric_drift_detective.notifications.outlook_channel import OutlookChannel
from fabric_drift_detective.notifications.slack_channel import SlackChannel
from fabric_drift_detective.notifications.teams_channel import TeamsChannel
from fabric_drift_detective.schema_diff import DriftRecord, DriftType, Severity


def _alert(pr_url: str | None = "https://github.com/x/y/pull/1") -> DriftAlert:
    return DriftAlert(
        drifts=[
            DriftRecord(
                layer=Layer.SILVER,
                drift_type=DriftType.COLUMN_RENAME,
                severity=Severity.CRITICAL,
                table="customers",
                column="email",
                old="email",
                new="email_address",
                downstream_impact=["gold:Dim_Customer.Email"],
                auto_fixable=True,
            ),
            DriftRecord(
                layer=Layer.BRONZE,
                drift_type=DriftType.COLUMN_ADD,
                severity=Severity.INFO,
                table="Customer",
                column="LoyaltyTier",
                new="VARCHAR",
            ),
        ],
        summary="1 critical rename.",
        pr_url=pr_url,
        environment="simulate",
    )


# ---------------------------------------------------------------- alert model
def test_counts_by_severity():
    counts = _alert().counts_by_severity
    assert counts["critical"] == 1 and counts["info"] == 1 and counts["warning"] == 0


def test_top_drifts_critical_first():
    top = _alert().top_drifts(1)
    assert top[0].severity is Severity.CRITICAL


# ---------------------------------------------------------------- slack
def test_slack_blocks_shape():
    payload = SlackChannel(webhook_url="http://hook").render(_alert())
    assert payload["blocks"][0]["type"] == "header"
    # PR button present
    actions = [b for b in payload["blocks"] if b["type"] == "actions"]
    assert actions and actions[0]["elements"][0]["url"].endswith("/pull/1")


def test_slack_bot_mode_includes_channel():
    payload = SlackChannel(mode="bot", bot_token="t", channel="#x").render(_alert())
    assert payload["channel"] == "#x"


@patch("fabric_drift_detective.notifications.slack_channel.requests.post")
def test_slack_webhook_send(mock_post):
    mock_post.return_value = MagicMock(status_code=200)
    SlackChannel(webhook_url="http://hook").send(_alert())
    assert mock_post.call_args[0][0] == "http://hook"


# ---------------------------------------------------------------- teams
def test_teams_webhook_envelope():
    payload = TeamsChannel(webhook_url="http://hook").render(_alert())
    assert payload["type"] == "message"
    card = payload["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard"
    assert card["actions"][0]["url"].endswith("/pull/1")
    # severity facts present
    fact_titles = " ".join(
        f["title"] for f in card["body"][1]["facts"]
    )
    assert "Critical" in fact_titles


def test_teams_graph_payload_attachment_reference():
    payload = TeamsChannel(mode="graph", team_id="t", channel_id="c").render(_alert())
    att_id = payload["attachments"][0]["id"]
    assert f'<attachment id="{att_id}">' in payload["body"]["content"]
    assert payload["attachments"][0]["contentType"] == (
        "application/vnd.microsoft.card.adaptive"
    )


# ---------------------------------------------------------------- outlook
def test_outlook_graph_payload():
    ch = OutlookChannel(sender="a@b.com", to=["x@b.com"], cc=["y@b.com"])
    payload = ch.render(_alert())
    msg = payload["message"]
    assert msg["body"]["contentType"] == "HTML"
    assert msg["toRecipients"][0]["emailAddress"]["address"] == "x@b.com"
    assert msg["ccRecipients"][0]["emailAddress"]["address"] == "y@b.com"
    assert "Severity summary" in msg["body"]["content"]
    assert "/pull/1" in msg["body"]["content"]


@patch("fabric_drift_detective.azure_auth.get_token", return_value="tok")
@patch("fabric_drift_detective.notifications.outlook_channel.requests.post")
def test_outlook_graph_send_url(mock_post, _tok):
    mock_post.return_value = MagicMock(status_code=202)
    OutlookChannel(sender="a@b.com", to=["x@b.com"]).send(_alert())
    assert "users/a@b.com/sendMail" in mock_post.call_args[0][0]


# ---------------------------------------------------------------- dispatcher
class _Boom(ConsoleChannel):
    name = "boom"

    def send(self, alert):  # noqa: D102
        raise RuntimeError("kaput")


def test_failing_channel_does_not_block_others(capsys):
    ok = ConsoleChannel()
    dispatcher = Dispatcher([_Boom(), ok])
    results = dispatcher.dispatch(_alert(pr_url=None))
    assert results["boom"].startswith("failed")
    assert results["console"] == "sent"


def test_dry_run_renders_without_sending():
    slack = SlackChannel(webhook_url="http://hook")
    with patch.object(slack, "send") as mock_send:
        results = Dispatcher([slack]).dispatch(_alert(), dry_run=True)
    mock_send.assert_not_called()
    assert results["slack"] == "rendered"


def test_build_dispatcher_respects_toggles():
    cfg = {
        "console": {"enabled": True},
        "slack": {"enabled": True, "mode": "webhook", "webhook_url": "http://h"},
        "teams": {"enabled": False},
        "outlook": {"enabled": False},
    }
    dispatcher = build_dispatcher(cfg)
    names = [c.name for c in dispatcher.channels]
    assert names == ["console", "slack"]
