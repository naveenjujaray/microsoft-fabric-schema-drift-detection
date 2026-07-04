"""Multi-channel drift notifications: console, Slack, Teams, Outlook."""

from .base import DriftAlert, NotificationChannel
from .dispatcher import Dispatcher, build_dispatcher

__all__ = ["DriftAlert", "NotificationChannel", "Dispatcher", "build_dispatcher"]
