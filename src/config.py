"""Configuration loading: config.yaml + .env, with ${VAR} interpolation.

No IDs or secrets are hardcoded anywhere in the codebase; everything
flows through this module.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


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
