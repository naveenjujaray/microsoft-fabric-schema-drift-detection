"""Slack channel: Block Kit via incoming webhook or chat.postMessage."""

from __future__ import annotations

from typing import Any

import requests

from .base import DriftAlert, NotificationChannel

_EMOJI = {"critical": ":red_circle:", "warning": ":large_yellow_circle:",
          "info": ":large_blue_circle:"}


class SlackChannel(NotificationChannel):
    """mode='webhook' posts to an incoming webhook URL;
    mode='bot' uses chat.postMessage with a bot token."""

    name = "slack"

    def __init__(
        self,
        mode: str = "webhook",
        webhook_url: str = "",
        # empty default is a disabled-channel sentinel, not a secret
        bot_token: str = "",  # noqa: S107 # nosec B107
        channel: str = "#data-alerts",
        timeout: int = 15,
    ) -> None:
        self.mode = mode
        self.webhook_url = webhook_url
        self.bot_token = bot_token
        self.channel = channel
        self.timeout = timeout

    # ------------------------------------------------------------------
    def render(self, alert: DriftAlert) -> dict[str, Any]:
        counts = alert.counts_by_severity
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": alert.title[:150]},
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"{_EMOJI[sev]} *{sev.title()}*: {count}",
                    }
                    for sev, count in counts.items()
                ],
            },
            {"type": "divider"},
        ]
        for d in alert.top_drifts(3):
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"{_EMOJI[d.severity.value]} `{d.layer.value}:"
                            f"{d.table}.{d.column or '*'}` — "
                            f"*{d.drift_type.value}* "
                            f"({d.old!r} → {d.new!r}); "
                            f"{len(d.downstream_impact)} downstream asset(s)"
                        ),
                    },
                }
            )
        if alert.summary:
            blocks.append(
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": alert.summary[:2900]}],
                }
            )
        if alert.pr_url:
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "View PR"},
                            "url": alert.pr_url,
                            "style": "primary",
                        }
                    ],
                }
            )
        payload: dict[str, Any] = {"blocks": blocks, "text": alert.title}
        if self.mode == "bot":
            payload["channel"] = self.channel
        return payload

    # ------------------------------------------------------------------
    def send(self, alert: DriftAlert) -> None:
        payload = self.render(alert)
        if self.mode == "bot":
            resp = requests.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {self.bot_token}"},
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Slack API error: {data.get('error')}")
        else:
            if not self.webhook_url:
                raise ValueError("slack webhook_url not configured")
            resp = requests.post(self.webhook_url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
