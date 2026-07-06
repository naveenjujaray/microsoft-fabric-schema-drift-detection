"""Fail-loud baseline policy in the CLI entry point.

Missing or partially missing baselines must abort the run with a
BaselineError (exit code 3) - never silently recapture.
"""

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
from fabric_drift_detective.cli import EXIT_BASELINE_ERROR, run_once
from fabric_drift_detective.schema_store import BaselineError, SchemaStore


class StubBackend(SchemaBackend):
    def __init__(self, layers: list[Layer]) -> None:
        self._layers = layers

    def list_layers(self) -> list[Layer]:
        return self._layers

    def get_schema(self, layer: Layer) -> LayerSchema:
        table = TableSchema(name="t")
        table.columns["c"] = ColumnSchema(name="c", dtype="INTEGER")
        return LayerSchema(layer=layer, tables={"t": table})


@pytest.fixture
def cfg(tmp_path):
    return {"baseline": {"dir": str(tmp_path / "baselines")}}


def _patch_backend(monkeypatch, layers):
    monkeypatch.setattr(
        main_mod, "make_backend", lambda mode, cfg: StubBackend(layers)
    )


def test_no_baselines_raises(monkeypatch, cfg):
    _patch_backend(monkeypatch, [Layer.SILVER])
    with pytest.raises(BaselineError, match="--baseline"):
        run_once("simulate", cfg)


def test_partial_baselines_raise_and_name_missing_layer(monkeypatch, cfg):
    _patch_backend(monkeypatch, [Layer.SILVER, Layer.GOLD])
    backend = StubBackend([Layer.SILVER])
    store = SchemaStore(cfg["baseline"]["dir"], keep_history=False)
    store.save_all(backend.get_all_schemas())  # silver only; gold missing

    with pytest.raises(BaselineError, match="gold"):
        run_once("simulate", cfg)


def test_complete_baselines_run_clean(monkeypatch, cfg):
    _patch_backend(monkeypatch, [Layer.SILVER])
    store = SchemaStore(cfg["baseline"]["dir"], keep_history=False)
    store.save_all(StubBackend([Layer.SILVER]).get_all_schemas())

    assert run_once("simulate", cfg) == 0  # no drift, no criticals


def test_cli_exit_code_for_missing_baselines(monkeypatch, cfg, tmp_path):
    _patch_backend(monkeypatch, [Layer.SILVER])
    monkeypatch.setattr(main_mod, "load_config", lambda path: cfg)
    exit_code = main_mod.main(["--mode", "simulate", "--once"])
    assert exit_code == EXIT_BASELINE_ERROR
