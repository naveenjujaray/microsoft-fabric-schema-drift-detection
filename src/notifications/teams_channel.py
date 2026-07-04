"""Microsoft Teams channel: Adaptive Card via webhook or Graph API.

mode='webhook': POST the card to an incoming-webhook / workflow URL
                wrapped in the standard message-attachment envelope.
mode='graph':   POST /teams/{team-id}/channels/{channel-id}/messages
                using the SAME ClientSecretCredential as Fabric
                (Graph application permission: ChannelMessage.Send,
                admin consent required — see docs/FABRIC_SETUP.md).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import requests

from .base import DriftAlert, NotificationChannel

_ICON = {"critical": "🔴", "warning": "🟡", "info": "🔵"}


class TeamsChannel(NotificationChannel):
    name = "teams"

    def __init__(
        self,
        mode: str = "webhook",
        webhook_url: str = "",
        team_id: str = "",
        channel_id: str = "",
        timeout: int = 15,
    ) -> None:
        self.mode = mode
        self.webhook_url = webhook_url
        self.team_id = team_id
        self.channel_id = channel_id
        self.timeout = timeout

    # ------------------------------------------------------------------
    def _adaptive_card(self, alert: DriftAlert) -> dict[str, Any]:
        counts = alert.counts_by_severity
        body: list[dict[str, Any]] = [
            {
                "type": "TextBlock",
                "size": "Large",
                "weight": "Bolder",
                "text": alert.title,
                "wrap": True,
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": f"{_ICON[sev]} {sev.title()}", "value": str(count)}
                    for sev, count in counts.items()
                ],
            },
            {
                "type": "TextBlock",
                "weight": "Bolder",
                "text": "Top drifts",
                "spacing": "Medium",
            },
        ]
        for d in alert.top_drifts(3):
            body.append(
                {
                    "type": "TextBlock",
                    "wrap": True,
                    "text": (
                        f"{_ICON[d.severity.value]} **{d.drift_type.value}** — "
                        f"`{d.layer.value}:{d.table}.{d.column or '*'}` "
                        f"({d.old!r} → {d.new!r}), "
                        f"{len(d.downstream_impact)} downstream"
                    ),
                }
            )
        if alert.summary:
            body.append(
                {"type": "TextBlock", "wrap": True, "isSubtle": True,
                 "text": alert.summary}
            )
        card: dict[str, Any] = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": body,
        }
        if alert.pr_url:
            card["actions"] = [
                {"type": "Action.OpenUrl", "title": "View PR", "url": alert.pr_url}
            ]
        return card

    def render(self, alert: DriftAlert) -> dict[str, Any]:
        card = self._adaptive_card(alert)
        if self.mode == "graph":
            attachment_id = str(uuid.uuid4())
            return {
                "body": {
                    "contentType": "html",
                    "content": f'<attachment id="{attachment_id}"></attachment>',
                },
                "attachments": [
                    {
                        "id": attachment_id,
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": json.dumps(card),
                    }
                ],
            }
        # webhook envelope
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": card,
                }
            ],
        }

    # ------------------------------------------------------------------
    def send(self, alert: DriftAlert) -> None:
        payload = self.render(alert)
        if self.mode == "graph":
            from ..azure_auth import GRAPH_SCOPE, get_token

            if not (self.team_id and self.channel_id):
                raise ValueError("teams graph mode needs team_id and channel_id")
            resp = requests.post(
                "https://graph.microsoft.com/v1.0/teams/"
                f"{self.team_id}/channels/{self.channel_id}/messages",
                headers={"Authorization": f"Bearer {get_token(GRAPH_SCOPE)}"},
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
        else:
            if not self.webhook_url:
                raise ValueError("teams webhook_url not configured")
            resp = requests.post(self.webhook_url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
