"""SchemaStore: persistence, integrity errors, fail-loud baseline policy."""

from __future__ import annotations

import json

import pytest

from src.backends.base import ColumnSchema, Layer, LayerSchema, TableSchema
from src.schema_store import BaselineError, SchemaStore


def _layer(layer: Layer = Layer.SILVER) -> LayerSchema:
    table = TableSchema(name="customers")
    table.columns["id"] = ColumnSchema(name="id", dtype="INTEGER", is_key=True)
    return LayerSchema(layer=layer, tables={"customers": table})


def test_save_and_load_roundtrip(tmp_path):
    store = SchemaStore(tmp_path / "b", keep_history=False)
    store.save(_layer())
    loaded = store.load(Layer.SILVER)
    assert loaded is not None
    assert loaded.tables["customers"].columns["id"].dtype == "INTEGER"


def test_save_writes_history_snapshot(tmp_path):
    store = SchemaStore(tmp_path / "b", keep_history=True)
    store.save(_layer())
    history = list((tmp_path / "b" / "history").glob("silver-*.json"))
    assert len(history) == 1


def test_load_missing_returns_none(tmp_path):
    store = SchemaStore(tmp_path / "b")
    assert store.load(Layer.GOLD) is None


def test_load_corrupt_json_raises_baseline_error(tmp_path):
    store = SchemaStore(tmp_path / "b")
    (tmp_path / "b" / "silver.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(BaselineError, match="corrupt"):
        store.load(Layer.SILVER)


def test_load_missing_schema_key_raises_baseline_error(tmp_path):
    store = SchemaStore(tmp_path / "b")
    (tmp_path / "b" / "silver.json").write_text(
        json.dumps({"captured_at": "2026-01-01"}), encoding="utf-8"
    )
    with pytest.raises(BaselineError):
        store.load(Layer.SILVER)


def test_missing_layers(tmp_path):
    store = SchemaStore(tmp_path / "b", keep_history=False)
    store.save(_layer(Layer.SILVER))
    missing = store.missing_layers([Layer.SILVER, Layer.GOLD, Layer.BRONZE])
    assert missing == [Layer.GOLD, Layer.BRONZE]
    assert store.missing_layers([Layer.SILVER]) == []


def test_atomic_write_leaves_no_temp_files(tmp_path):
    store = SchemaStore(tmp_path / "b", keep_history=True)
    store.save(_layer())
    leftovers = list((tmp_path / "b").rglob("*.tmp"))
    assert leftovers == []
