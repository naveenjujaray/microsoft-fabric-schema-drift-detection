"""Outlook email channel: Graph sendMail with SMTP fallback.

mode='graph': POST /users/{sender}/sendMail with the SAME
              ClientSecretCredential as Fabric (Graph application
              permission: Mail.Send, admin consent required).
mode='smtp':  plain SMTP for tenants where Mail.Send isn't granted
              (SMTP_USERNAME / SMTP_PASSWORD env vars).
"""

from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Any

import requests

from .base import DriftAlert, NotificationChannel

_COLOR = {"critical": "#d13438", "warning": "#ffb900", "info": "#0078d4"}


def _severity_table(alert: DriftAlert) -> str:
    rows = "".join(
        f"<tr><td style='padding:4px 12px;color:{_COLOR[sev]};font-weight:bold'>"
        f"{sev.title()}</td><td style='padding:4px 12px'>{count}</td></tr>"
        for sev, count in alert.counts_by_severity.items()
    )
    return (
        "<table border='1' cellspacing='0' style='border-collapse:collapse'>"
        f"<tr><th style='padding:4px 12px'>Severity</th>"
        f"<th style='padding:4px 12px'>Count</th></tr>{rows}</table>"
    )


def _layer_table(alert: DriftAlert) -> str:
    rows = "".join(
        f"<tr><td style='padding:4px 12px'>{escape(layer)}</td>"
        f"<td style='padding:4px 12px'>{count}</td></tr>"
        for layer, count in alert.counts_by_layer.items()
    )
    return (
        "<table border='1' cellspacing='0' style='border-collapse:collapse'>"
        f"<tr><th style='padding:4px 12px'>Layer</th>"
        f"<th style='padding:4px 12px'>Drifts</th></tr>{rows}</table>"
    )


def _drift_rows(alert: DriftAlert) -> str:
    rows = "".join(
        "<tr>"
        f"<td style='padding:4px 8px;color:{_COLOR[d.severity.value]}'>"
        f"{d.severity.value}</td>"
        f"<td style='padding:4px 8px'>{escape(d.layer.value)}</td>"
        f"<td style='padding:4px 8px'>{escape(d.table)}"
        f"{('.' + escape(d.column)) if d.column else ''}</td>"
        f"<td style='padding:4px 8px'>{escape(d.drift_type.value)}</td>"
        f"<td style='padding:4px 8px'>{escape(repr(d.old))} &rarr; "
        f"{escape(repr(d.new))}</td>"
        f"<td style='padding:4px 8px;text-align:right'>"
        f"{len(d.downstream_impact)}</td>"
        "</tr>"
        for d in alert.drifts
    )
    return (
        "<table border='1' cellspacing='0' style='border-collapse:collapse'>"
        "<tr><th>Severity</th><th>Layer</th><th>Object</th><th>Drift</th>"
        "<th>Change</th><th>Downstream</th></tr>"
        f"{rows}</table>"
    )


class OutlookChannel(NotificationChannel):
    name = "outlook"

    def __init__(
        self,
        mode: str = "graph",
        sender: str = "",
        to: list[str] | None = None,
        cc: list[str] | None = None,
        smtp_host: str = "",
        smtp_port: int = 587,
        timeout: int = 20,
    ) -> None:
        self.mode = mode
        self.sender = sender
        self.to = to or []
        self.cc = cc or []
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.timeout = timeout

    # ------------------------------------------------------------------
    def _html_body(self, alert: DriftAlert) -> str:
        pr_link = (
            f"<p><a href='{alert.pr_url}' style='font-weight:bold'>"
            "View the auto-created fix PR</a></p>"
            if alert.pr_url
            else ""
        )
        summary = (
            f"<p><i>{escape(alert.summary)}</i></p>" if alert.summary else ""
        )
        return (
            f"<html><body style='font-family:Segoe UI,Arial,sans-serif'>"
            f"<h2>{escape(alert.title)}</h2>"
            f"{summary}"
            f"<h3>Severity summary</h3>{_severity_table(alert)}"
            f"<h3>Per-layer breakdown</h3>{_layer_table(alert)}"
            f"<h3>All drifts</h3>{_drift_rows(alert)}"
            f"{pr_link}"
            f"<p style='color:#888'>Generated {alert.generated_at.isoformat()} "
            f"by Fabric Schema Drift Detective ({alert.environment} mode)</p>"
            "</body></html>"
        )

    def render(self, alert: DriftAlert) -> dict[str, Any]:
        """Graph sendMail payload (also the source of truth for SMTP)."""
        return {
            "message": {
                "subject": alert.title,
                "body": {"contentType": "HTML", "content": self._html_body(alert)},
                "toRecipients": [
                    {"emailAddress": {"address": a}} for a in self.to
                ],
                "ccRecipients": [
                    {"emailAddress": {"address": a}} for a in self.cc
                ],
            },
            "saveToSentItems": True,
        }

    # ------------------------------------------------------------------
    def send(self, alert: DriftAlert) -> None:
        if self.mode == "smtp":
            self._send_smtp(alert)
            return
        from ..azure_auth import GRAPH_SCOPE, get_token

        if not self.sender or not self.to:
            raise ValueError("outlook graph mode needs sender and to[]")
        resp = requests.post(
            f"https://graph.microsoft.com/v1.0/users/{self.sender}/sendMail",
            headers={"Authorization": f"Bearer {get_token(GRAPH_SCOPE)}"},
            json=self.render(alert),
            timeout=self.timeout,
        )
        resp.raise_for_status()  # 202 Accepted on success

    def _send_smtp(self, alert: DriftAlert) -> None:
        if not (self.smtp_host and self.sender and self.to):
            raise ValueError("outlook smtp mode needs smtp_host, sender, to[]")
        msg = MIMEMultipart("alternative")
        msg["Subject"] = alert.title
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.to)
        if self.cc:
            msg["Cc"] = ", ".join(self.cc)
        msg.attach(MIMEText(self._html_body(alert), "html"))
        username = os.environ.get("SMTP_USERNAME", self.sender)
        password = os.environ.get("SMTP_PASSWORD", "")
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self.timeout) as s:
            s.starttls()
            if password:
                s.login(username, password)
            s.sendmail(self.sender, self.to + self.cc, msg.as_string())
