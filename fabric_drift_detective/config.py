"""Configuration loading: config.yaml + .env, with ${VAR} interpolation.

No IDs or secrets are hardcoded anywhere in the codebase; everything
flows through this module.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from .backends.base import Layer

logger = logging.getLogger(__name__)

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

_WATCH_MODES = ("full", "boundaries")


@dataclass
class WatchConfig:
    """Per-layer drift-watch scope (``watch:`` block in config.yaml).

    ``layers`` empty = watch every layer the backend advertises.
    ``mode``:
      * ``full`` — report every drift (default, original behavior);
      * ``boundaries`` — suppress intra-layer records and report only
        the lineage-synthesized cross_layer_break /
        cross_workspace_break records. For contract-enforced layers
        ("my Silver can't drift internally") only boundary breakage
        matters.
    """

    layers: list[Layer] = field(default_factory=list)
    mode: str = "full"

    def includes(self, layer: Layer) -> bool:
        return not self.layers or layer in self.layers


def parse_watch_config(cfg: dict[str, Any]) -> WatchConfig:
    """Parse+validate the ``watch:`` block; absent block = full watch."""
    watch = cfg.get("watch", {}) or {}
    raw_layers = watch.get("layers", []) or []
    layers: list[Layer] = []
    for name in raw_layers:
        try:
            layers.append(Layer(str(name)))
        except ValueError as exc:
            raise ValueError(
                f"watch.layers contains unknown layer {name!r}; "
                f"valid: {[layer.value for layer in Layer]}"
            ) from exc
    mode = str(watch.get("mode", "full")).strip().lower()
    if mode not in _WATCH_MODES:
        raise ValueError(
            f"watch.mode must be one of {_WATCH_MODES}, got {mode!r}"
        )
    return WatchConfig(layers=layers, mode=mode)


def _resolve_env(match: re.Match[str]) -> str:
    name = match.group(1)
    value = os.environ.get(name)
    if value is None:
        logger.warning(
            "config placeholder ${%s} has no environment value; "
            "substituting empty string", name,
        )
        return ""
    return value


def _interpolate(value: Any) -> Any:
    """Recursively replace ${VAR} placeholders with environment values."""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(_resolve_env, value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    """Load config.yaml, expanding ${ENV_VAR} placeholders.

    Also loads a sibling .env file (if present) into the process
    environment first, so placeholders can resolve from it.
    """
    path = Path(path)
    load_dotenv(path.parent / ".env")
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a YAML mapping at the top level")
    result: dict[str, Any] = _interpolate(raw)
    return result
