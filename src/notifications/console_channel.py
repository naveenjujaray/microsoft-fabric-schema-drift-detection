"""Rich console channel - always on, zero external dependencies."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from .base import DriftAlert, NotificationChannel

_SEVERITY_STYLE = {"critical": "bold red", "warning": "yellow", "info": "cyan"}


class ConsoleChannel(NotificationChannel):
    """Pretty drift report on stdout."""

    name = "console"

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def render(self, alert: DriftAlert) -> Table:
        table = Table(title=alert.title, show_lines=False)
        table.add_column("Severity", style="bold")
        table.add_column("Layer")
        table.add_column("Table")
        table.add_column("Column")
        table.add_column("Drift")
        table.add_column("Old -> New")
        table.add_column("Downstream", justify="right")
        for d in sorted(
            alert.drifts, key=lambda x: ("critical", "warning", "info").index(
                x.severity.value
            )
        ):
            table.add_row(
                f"[{_SEVERITY_STYLE[d.severity.value]}]{d.severity.value}[/]",
                d.layer.value,
                escape(d.table),
                escape(d.column or "-"),
                d.drift_type.value,
                escape(f"{d.old!r} -> {d.new!r}"),
                str(len(d.downstream_impact)),
            )
        return table

    def send(self, alert: DriftAlert) -> None:
        self.console.print(self.render(alert))
        if alert.summary:
            self.console.print(Panel(escape(alert.summary), title="Impact summary"))
        if alert.pr_url:
            self.console.print(f"[bold green]PR:[/] {alert.pr_url}")
