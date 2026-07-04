"""Fan-out dispatcher: one alert, many channels, isolated failures.

Each channel send is wrapped in try/except so one failing channel never
blocks the others; per-channel success/failure is logged and returned.
``dry_run=True`` renders every enabled channel's payload to the console
without sending anything.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from rich.console import Console
from rich.panel import Panel

from .base import DriftAlert, NotificationChannel
from .console_channel import ConsoleChannel
from .outlook_channel import OutlookChannel
from .slack_channel import SlackChannel
from .teams_channel import TeamsChannel

logger = logging.getLogger(__name__)


class Dispatcher:
    """Sends one DriftAlert to every registered channel."""

    def __init__(self, channels: list[NotificationChannel]) -> None:
        self.channels = channels

    def dispatch(self, alert: DriftAlert, dry_run: bool = False) -> dict[str, str]:
        """Returns {channel_name: 'sent' | 'rendered' | 'failed: ...'}."""
        results: dict[str, str] = {}
        console = Console()
        for channel in self.channels:
            try:
                if dry_run:
                    payload = channel.render(alert)
                    if isinstance(channel, ConsoleChannel):
                        channel.send(alert)  # console is safe to "send" in dry-run
                    else:
                        console.print(
                            Panel(
                                json.dumps(payload, indent=2, default=str)[:4000],
                                title=f"[dry-run] {channel.name} payload",
                            )
                        )
                    results[channel.name] = "rendered"
                else:
                    channel.send(alert)
                    results[channel.name] = "sent"
                    logger.info("notification sent via %s", channel.name)
            except Exception as exc:  # noqa: BLE001 - isolation is the point
                results[channel.name] = f"failed: {exc}"
                logger.error("channel %s failed: %s", channel.name, exc)
        return results


def build_dispatcher(notif_config: dict[str, Any]) -> Dispatcher:
    """Construct channels from the config.yaml ``notifications`` block."""
    channels: list[NotificationChannel] = []

    if notif_config.get("console", {}).get("enabled", True):
        channels.append(ConsoleChannel())

    slack = notif_config.get("slack", {})
    if slack.get("enabled"):
        channels.append(
            SlackChannel(
                mode=slack.get("mode", "webhook"),
                webhook_url=slack.get("webhook_url", ""),
                bot_token=slack.get("bot_token", ""),
                channel=slack.get("channel", "#data-alerts"),
            )
        )

    teams = notif_config.get("teams", {})
    if teams.get("enabled"):
        channels.append(
            TeamsChannel(
                mode=teams.get("mode", "webhook"),
                webhook_url=teams.get("webhook_url", ""),
                team_id=teams.get("team_id", ""),
                channel_id=teams.get("channel_id", ""),
            )
        )

    outlook = notif_config.get("outlook", {})
    if outlook.get("enabled"):
        channels.append(
            OutlookChannel(
                mode=outlook.get("mode", "graph"),
                sender=outlook.get("sender", ""),
                to=list(outlook.get("to", [])),
                cc=list(outlook.get("cc", [])),
                smtp_host=outlook.get("smtp_host", ""),
                smtp_port=int(outlook.get("smtp_port", 587)),
            )
        )

    return Dispatcher(channels)
