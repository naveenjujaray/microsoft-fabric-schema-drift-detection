"""Channel-agnostic DriftAlert model + NotificationChannel ABC.

The alert is built ONCE from the drift run; each channel's formatter
translates it into its native payload (Adaptive Card / HTML / Block
Kit). Content logic lives here, presentation lives per channel.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..schema_diff import DriftRecord, Severity

_SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}


@dataclass
class DriftAlert:
    """Everything any channel needs to render a drift notification."""

    drifts: list[DriftRecord]
    summary: str = ""
    pr_url: str | None = None
    pr_title: str | None = None
    environment: str = "simulate"
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    # ------------------------------------------------------------------
    @property
    def counts_by_severity(self) -> dict[str, int]:
        counts = {s.value: 0 for s in Severity}
        for d in self.drifts:
            counts[d.severity.value] += 1
        return counts

    @property
    def counts_by_layer(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for d in self.drifts:
            counts[d.layer.value] = counts.get(d.layer.value, 0) + 1
        return counts

    def top_drifts(self, n: int = 3) -> list[DriftRecord]:
        """Most severe drifts first (critical > warning > info)."""
        return sorted(self.drifts, key=lambda d: _SEVERITY_ORDER[d.severity])[:n]

    @property
    def title(self) -> str:
        crit = self.counts_by_severity["critical"]
        total = len(self.drifts)
        icon = "🔴" if crit else ("🟡" if total else "🟢")
        return (
            f"{icon} Schema drift: {total} change(s), "
            f"{crit} critical ({self.environment})"
        )


class NotificationChannel(ABC):
    """One delivery target. ``render`` builds the payload; ``send``
    delivers it. ``--dry-run`` calls only ``render``."""

    name: str = "channel"

    @abstractmethod
    def render(self, alert: DriftAlert) -> Any:
        """Build the channel-native payload without sending."""

    @abstractmethod
    def send(self, alert: DriftAlert) -> None:
        """Deliver the alert. Raise on failure; dispatcher isolates it."""
