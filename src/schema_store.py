"""Baseline snapshot persistence: one JSON file per layer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .backends.base import Layer, LayerSchema


class SchemaStore:
    """Persists per-layer schema baselines under a directory.

    With ``keep_history=True`` (default) every save also archives a
    timestamped copy under ``<dir>/history/`` so drift trends can be
    analyzed later (see the ``historian`` agent).
    """

    def __init__(
        self, directory: str | Path = ".baselines", keep_history: bool = True
    ) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.keep_history = keep_history

    def _path(self, layer: Layer) -> Path:
        return self.directory / f"{layer.value}.json"

    def save(self, schema: LayerSchema) -> Path:
        """Write (overwrite) the baseline for one layer."""
        path = self._path(schema.layer)
        payload = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "schema": schema.to_dict(),
        }
        text = json.dumps(payload, indent=2)
        path.write_text(text, encoding="utf-8")
        if self.keep_history:
            history = self.directory / "history"
            history.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            (history / f"{schema.layer.value}-{stamp}.json").write_text(
                text, encoding="utf-8"
            )
        return path

    def save_all(self, schemas: dict[Layer, LayerSchema]) -> None:
        for schema in schemas.values():
            self.save(schema)

    def load(self, layer: Layer) -> LayerSchema | None:
        """Load one layer's baseline, or None if not captured yet."""
        path = self._path(layer)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return LayerSchema.from_dict(payload["schema"])

    def load_all(self) -> dict[Layer, LayerSchema]:
        baselines: dict[Layer, LayerSchema] = {}
        for layer in Layer:
            schema = self.load(layer)
            if schema is not None:
                baselines[layer] = schema
        return baselines

    def has_baselines(self) -> bool:
        return any(self._path(layer).exists() for layer in Layer)
