"""Drift engine tests: every drift type."""

from __future__ import annotations

import copy

from src.backends.base import ColumnSchema, Layer, LayerSchema
from src.schema_diff import DriftType, Severity, diff_layer
from tests.conftest import make_table


def _clone(schema: LayerSchema) -> LayerSchema:
    return copy.deepcopy(schema)


def _types(drifts) -> set[DriftType]:
    return {d.drift_type for d in drifts}


def test_no_drift(silver_baseline):
    assert diff_layer(silver_baseline, _clone(silver_baseline)) == []


def test_column_drop_is_critical(silver_baseline):
    cur = _clone(silver_baseline)
    del cur.tables["orders"].columns["freight"]
    drifts = diff_layer(silver_baseline, cur)
    assert len(drifts) == 1
    d = drifts[0]
    assert d.drift_type is DriftType.COLUMN_DROP
    assert d.severity is Severity.CRITICAL
    assert d.table == "orders" and d.column == "freight"
    assert not d.auto_fixable


def test_column_add_is_info(silver_baseline):
    cur = _clone(silver_baseline)
    cur.tables["customers"].columns["loyalty"] = ColumnSchema(
        name="loyalty", dtype="VARCHAR", ordinal=9
    )
    drifts = diff_layer(silver_baseline, cur)
    assert _types(drifts) == {DriftType.COLUMN_ADD}
    assert drifts[0].severity is Severity.INFO


def test_unsafe_type_change_is_critical(silver_baseline):
    cur = _clone(silver_baseline)
    cur.tables["orders"].columns["freight"] = ColumnSchema(
        name="freight", dtype="VARCHAR", ordinal=1
    )
    drifts = diff_layer(silver_baseline, cur)
    assert _types(drifts) == {DriftType.TYPE_CHANGE}
    assert drifts[0].severity is Severity.CRITICAL
    assert not drifts[0].auto_fixable


def test_safe_type_widening_is_warning(silver_baseline):
    cur = _clone(silver_baseline)
    cur.tables["customers"].columns["customer_id"] = ColumnSchema(
        name="customer_id", dtype="BIGINT", nullable=False, ordinal=0, is_key=True
    )
    drifts = diff_layer(silver_baseline, cur)
    assert _types(drifts) == {DriftType.TYPE_CHANGE}
    assert drifts[0].severity is Severity.WARNING
    assert drifts[0].auto_fixable


def test_rename_detected_by_similarity_and_position(silver_baseline):
    cur = _clone(silver_baseline)
    col = cur.tables["customers"].columns.pop("email")
    cur.tables["customers"].columns["email_address"] = ColumnSchema(
        name="email_address", dtype=col.dtype, nullable=col.nullable,
        ordinal=col.ordinal,
    )
    drifts = diff_layer(silver_baseline, cur)
    assert _types(drifts) == {DriftType.COLUMN_RENAME}
    d = drifts[0]
    assert (d.old, d.new) == ("email", "email_address")
    assert d.severity is Severity.CRITICAL
    assert d.auto_fixable


def test_unrelated_drop_add_not_paired_as_rename(silver_baseline):
    """Different type + dissimilar name + different position -> drop + add."""
    cur = _clone(silver_baseline)
    cur.tables["customers"].columns.pop("phone")
    cur.tables["customers"].columns["signup_ts"] = ColumnSchema(
        name="signup_ts", dtype="TIMESTAMP", ordinal=7
    )
    drifts = diff_layer(silver_baseline, cur)
    assert _types(drifts) == {DriftType.COLUMN_DROP, DriftType.COLUMN_ADD}


def test_nullability_change_is_warning(silver_baseline):
    cur = _clone(silver_baseline)
    cur.tables["orders"].columns["total"] = ColumnSchema(
        name="total", dtype="DECIMAL(19,4)", nullable=True, ordinal=2
    )
    drifts = diff_layer(silver_baseline, cur)
    assert _types(drifts) == {DriftType.NULLABILITY_CHANGE}
    assert drifts[0].severity is Severity.WARNING


def test_key_change_is_critical(silver_baseline):
    cur = _clone(silver_baseline)
    cur.tables["orders"].columns["order_id"] = ColumnSchema(
        name="order_id", dtype="INTEGER", nullable=False, ordinal=0, is_key=False
    )
    drifts = diff_layer(silver_baseline, cur)
    assert _types(drifts) == {DriftType.KEY_CHANGE}
    assert drifts[0].severity is Severity.CRITICAL


def test_table_drop_and_add(silver_baseline):
    cur = _clone(silver_baseline)
    del cur.tables["orders"]
    cur.tables["returns"] = make_table(
        "returns", [("return_id", "INTEGER", False, True)]
    )
    drifts = diff_layer(silver_baseline, cur)
    by_type = {d.drift_type: d for d in drifts}
    assert by_type[DriftType.TABLE_DROP].severity is Severity.CRITICAL
    assert by_type[DriftType.TABLE_ADD].severity is Severity.INFO


def test_diff_rejects_mismatched_layers(silver_baseline):
    import pytest

    other = LayerSchema(layer=Layer.GOLD)
    with pytest.raises(ValueError):
        diff_layer(silver_baseline, other)
