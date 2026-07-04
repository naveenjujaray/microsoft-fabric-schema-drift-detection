"""Configuration loading: config.yaml + .env, with ${VAR} interpolation.

No IDs or secrets are hardcoded anywhere in the codebase; everything
flows through this module.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _interpolate(value: Any) -> Any:
    """Recursively replace ${VAR} placeholders with environment values."""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
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
    return _interpolate(raw)
