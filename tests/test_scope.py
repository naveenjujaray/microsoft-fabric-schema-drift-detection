"""Watch scope end-to-end: layer filtering + boundaries mode in run_once."""

from __future__ import annotations

import pytest

from fabric_drift_detective import cli as main_mod
from fabric_drift_detective.backends.base import (
    ColumnSchema,
    Layer,
    LayerSchema,
    SchemaBackend,
    TableSchema,
)
from fabric_drift_detective.cli import run_once
from fabric_drift_detective.schema_store import SchemaStore


class RecordingBackend(SchemaBackend):
    """Silver 'customers' table; records every get_schema call."""

    def __init__(self, layers: list[Layer], drop_email: bool = False) -> None:
        self._layers = layers
        self.drop_email = drop_email
        self.queried: list[Layer] = []

    def list_layers(self) -> list[Layer]:
        return self._layers

    def get_schema(self, layer: Layer) -> LayerSchema:
        self.queried.append(layer)
        table = TableSchema(name="customers")
        table.columns["customer_id"] = ColumnSchema(
            name="customer_id", dtype="INTEGER", nullable=False, ordinal=0,
        )
        if not self.drop_email:
            table.columns["email"] = ColumnSchema(
                name="email", dtype="VARCHAR", ordinal=1
            )
        return LayerSchema(layer=layer, tables={"customers": table})


@pytest.fixture
def cfg(tmp_path):
    return {
        "baseline": {"dir": str(tmp_path / "baselines")},
        "lineage": {"workspaces_manifest": ""},  # single-workspace for tests
    }


def _seed_baselines(cfg, layers):
    backend = RecordingBackend(layers)
    SchemaStore(cfg["baseline"]["dir"], keep_history=False).save_all(
        {layer: backend.get_schema(layer) for layer in layers}
    )


def test_unwatched_layers_never_queried(monkeypatch, cfg):
    backend = RecordingBackend([Layer.BRONZE, Layer.SILVER, Layer.GOLD])
    monkeypatch.setattr(main_mod, "make_backend", lambda m, c: backend)
    _seed_baselines(cfg, [Layer.BRONZE, Layer.SILVER])
    cfg["watch"] = {"layers": ["bronze", "silver"]}

    run_once("simulate", cfg)

    assert Layer.GOLD not in backend.queried
    assert set(backend.queried) == {Layer.BRONZE, Layer.SILVER}


def test_boundaries_mode_suppresses_intra_layer_keeps_breaks(monkeypatch, cfg):
    """Dropping silver.customers.email: full mode reports the drop AND the
    lineage break in Gold; boundaries mode reports ONLY the break."""
    _seed_baselines(cfg, [Layer.SILVER])
    drifted = RecordingBackend([Layer.SILVER], drop_email=True)
    monkeypatch.setattr(main_mod, "make_backend", lambda m, c: drifted)

    cfg["watch"] = {"mode": "full"}
    full_criticals = run_once("simulate", cfg, dry_run=True)

    cfg["watch"] = {"mode": "boundaries"}
    boundary_criticals = run_once("simulate", cfg, dry_run=True)

    # demo lineage: silver:customers.email -> gold:Dim_Customer.Email
    assert full_criticals == 2      # column_drop + cross_layer_break
    assert boundary_criticals == 1  # cross_layer_break only


def test_boundaries_mode_with_no_breaks_reports_clean(monkeypatch, cfg):
    """An intra-layer-only change (no lineage downstream) disappears in
    boundaries mode -> clean run."""
    _seed_baselines(cfg, [Layer.GOLD])
    # baseline Gold had email; current drops it; gold:customers.* has no
    # downstream edges in the demo graph
    drifted = RecordingBackend([Layer.GOLD], drop_email=True)
    monkeypatch.setattr(main_mod, "make_backend", lambda m, c: drifted)
    cfg["watch"] = {"mode": "boundaries"}

    assert run_once("simulate", cfg, dry_run=True) == 0


def test_default_config_full_watch_unchanged(monkeypatch, cfg):
    backend = RecordingBackend([Layer.SILVER, Layer.GOLD])
    monkeypatch.setattr(main_mod, "make_backend", lambda m, c: backend)
    _seed_baselines(cfg, [Layer.SILVER, Layer.GOLD])

    assert run_once("simulate", cfg) == 0
    assert set(backend.queried) >= {Layer.SILVER, Layer.GOLD}
