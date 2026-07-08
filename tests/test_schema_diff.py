"""Drift engine tests: every drift type."""

from __future__ import annotations

import copy

from fabric_drift_detective.backends.base import (
    ColumnSchema,
    Layer,
    LayerSchema,
    TableSchema,
)
from fabric_drift_detective.schema_diff import DriftType, Severity, diff_layer
from tests.conftest import make_table


def _clone(schema: LayerSchema) -> LayerSchema:
    return copy.deepcopy(schema)


def _types(drifts) -> set[DriftType]:
    return {d.drift_type for d in drifts}


def test_no_drift(silver_baseline):
    assert diff_layer(silver_baseline, _clone(silver_baseline)) == []


def _replace_col(schema: LayerSchema, table: str, col: str, **overrides):
    old = schema.tables[table].columns[col]
    fields = {
        "name": old.name, "dtype": old.dtype, "nullable": old.nullable,
        "ordinal": old.ordinal, "is_key": old.is_key,
        "default": old.default, "flags": old.flags,
    }
    fields.update(overrides)
    schema.tables[table].columns[col] = ColumnSchema(**fields)


def test_default_change_is_warning(silver_baseline):
    cur = _clone(silver_baseline)
    _replace_col(cur, "orders", "freight", default="0.0")
    drifts = diff_layer(silver_baseline, cur)
    assert _types(drifts) == {DriftType.DEFAULT_CHANGE}
    d = drifts[0]
    assert d.severity is Severity.WARNING
    assert not d.auto_fixable
    assert d.old is None and d.new == "0.0"
    assert d.table == "orders" and d.column == "freight"


def test_flag_change_is_warning(silver_baseline):
    cur = _clone(silver_baseline)
    _replace_col(cur, "orders", "freight", flags=("identity",))
    drifts = diff_layer(silver_baseline, cur)
    assert _types(drifts) == {DriftType.FLAG_CHANGE}
    d = drifts[0]
    assert d.severity is Severity.WARNING
    assert not d.auto_fixable


def test_flag_order_is_not_drift(silver_baseline):
    base = _clone(silver_baseline)
    _replace_col(base, "orders", "freight", flags=("computed", "identity"))
    cur = _clone(silver_baseline)
    _replace_col(cur, "orders", "freight", flags=("identity", "computed"))
    assert diff_layer(base, cur) == []


def test_column_schema_default_flags_roundtrip():
    col = ColumnSchema(name="x", dtype="INT", default="42",
                       flags=("identity",))
    assert ColumnSchema.from_dict(col.to_dict()) == col
    # baselines written before default/flags existed still load
    legacy = {"name": "x", "dtype": "INT"}
    old = ColumnSchema.from_dict(legacy)
    assert old.default is None and old.flags == ()


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


def test_precision_narrowing_is_critical(silver_baseline):
    """DECIMAL(19,4) -> DECIMAL(10,2): money truncation, base type unchanged."""
    cur = _clone(silver_baseline)
    cur.tables["orders"].columns["total"] = ColumnSchema(
        name="total", dtype="DECIMAL(10,2)", nullable=False, ordinal=2
    )
    drifts = diff_layer(silver_baseline, cur)
    assert _types(drifts) == {DriftType.PRECISION_SCALE_CHANGE}
    assert drifts[0].severity is Severity.CRITICAL
    assert not drifts[0].auto_fixable
    assert (drifts[0].old, drifts[0].new) == ("DECIMAL(19,4)", "DECIMAL(10,2)")


def test_precision_widening_is_warning_and_fixable(silver_baseline):
    cur = _clone(silver_baseline)
    cur.tables["orders"].columns["total"] = ColumnSchema(
        name="total", dtype="DECIMAL(28,8)", nullable=False, ordinal=2
    )
    drifts = diff_layer(silver_baseline, cur)
    assert _types(drifts) == {DriftType.PRECISION_SCALE_CHANGE}
    assert drifts[0].severity is Severity.WARNING
    assert drifts[0].auto_fixable


def test_precision_change_not_double_reported_as_type_change(silver_baseline):
    """Same base type must not also fire type_change."""
    cur = _clone(silver_baseline)
    cur.tables["orders"].columns["freight"] = ColumnSchema(
        name="freight", dtype="DECIMAL(9,2)", nullable=True, ordinal=1
    )
    drifts = diff_layer(silver_baseline, cur)
    assert DriftType.TYPE_CHANGE not in _types(drifts)
    assert DriftType.PRECISION_SCALE_CHANGE in _types(drifts)


def test_column_reorder_detected(silver_baseline):
    cur = _clone(silver_baseline)
    cols = cur.tables["customers"].columns
    cols["email"] = ColumnSchema(name="email", dtype="VARCHAR", ordinal=2)
    cols["phone"] = ColumnSchema(name="phone", dtype="VARCHAR", ordinal=1)
    drifts = diff_layer(silver_baseline, cur)
    assert _types(drifts) == {DriftType.COLUMN_REORDER}
    assert {d.column for d in drifts} == {"email", "phone"}
    assert drifts[0].severity is Severity.WARNING


def test_add_in_middle_does_not_false_positive_reorder(silver_baseline):
    """Inserting a column shifts absolute ordinals but not relative order."""
    cur = _clone(silver_baseline)
    cur.tables["customers"].columns["phone"] = ColumnSchema(
        name="phone", dtype="VARCHAR", ordinal=99
    )
    cur.tables["customers"].columns["mid"] = ColumnSchema(
        name="mid", dtype="VARCHAR", ordinal=1
    )
    drifts = diff_layer(silver_baseline, cur)
    assert DriftType.COLUMN_REORDER not in _types(drifts)
    assert _types(drifts) == {DriftType.COLUMN_ADD}


def _model_layer(measures: dict[str, str]) -> LayerSchema:
    t = TableSchema(name="Sales")
    t.columns["Amount"] = ColumnSchema(name="Amount", dtype="DECIMAL", ordinal=0)
    t.measures = dict(measures)
    return LayerSchema(layer=Layer.SEMANTIC_MODEL, tables={"Sales": t})


def test_measure_drop_is_critical():
    base = _model_layer({"Revenue": "SUM(Sales[Amount])"})
    cur = _model_layer({})
    drifts = diff_layer(base, cur)
    assert _types(drifts) == {DriftType.MEASURE_DROP}
    assert drifts[0].severity is Severity.CRITICAL
    assert drifts[0].column == "Revenue" and not drifts[0].auto_fixable


def test_measure_add_is_info():
    base = _model_layer({})
    cur = _model_layer({"Revenue": "SUM(Sales[Amount])"})
    drifts = diff_layer(base, cur)
    assert _types(drifts) == {DriftType.MEASURE_ADD}
    assert drifts[0].severity is Severity.INFO


def test_measure_change_is_warning():
    base = _model_layer({"Revenue": "SUM(Sales[Amount])"})
    cur = _model_layer({"Revenue": "SUM(Sales[Amount]) * 1.1"})
    drifts = diff_layer(base, cur)
    assert _types(drifts) == {DriftType.MEASURE_CHANGE}
    assert drifts[0].severity is Severity.WARNING


def test_measure_whitespace_only_change_ignored():
    base = _model_layer({"Revenue": "SUM(Sales[Amount])"})
    cur = _model_layer({"Revenue": "SUM(  Sales[Amount]  )"})
    assert diff_layer(base, cur) == []


def test_diff_rejects_mismatched_layers(silver_baseline):
    import pytest

    other = LayerSchema(layer=Layer.GOLD)
    with pytest.raises(ValueError):
        diff_layer(silver_baseline, other)


# ---------------------------------------------------------------------------
# deterministic rename detection (stable matching + confidence)
# ---------------------------------------------------------------------------
from fabric_drift_detective.schema_diff import _detect_renames  # noqa: E402


def _col(name, dtype="VARCHAR", nullable=True, ordinal=0, is_key=False):
    return ColumnSchema(
        name=name, dtype=dtype, nullable=nullable, ordinal=ordinal, is_key=is_key
    )


def test_rename_carries_confidence(silver_baseline):
    cur = _clone(silver_baseline)
    col = cur.tables["customers"].columns.pop("email")
    cur.tables["customers"].columns["email_address"] = ColumnSchema(
        name="email_address", dtype=col.dtype, nullable=col.nullable,
        ordinal=col.ordinal,
    )
    drifts = diff_layer(silver_baseline, cur)
    assert drifts[0].drift_type is DriftType.COLUMN_RENAME
    assert drifts[0].confidence is not None
    assert 0.5 < drifts[0].confidence <= 1.0


def test_rename_matching_is_order_independent():
    """Same pairs regardless of dict insertion order."""
    dropped_a = {"email": _col("email", ordinal=1), "mail2": _col("mail2", ordinal=2)}
    dropped_b = dict(reversed(list(dropped_a.items())))
    added_a = {
        "email_addr": _col("email_addr", ordinal=1),
        "mail2_new": _col("mail2_new", ordinal=2),
    }
    added_b = dict(reversed(list(added_a.items())))

    result_ab = [(o.name, n.name) for o, n, _ in _detect_renames(dropped_a, added_a)]
    result_ba = [(o.name, n.name) for o, n, _ in _detect_renames(dropped_b, added_b)]
    assert result_ab == result_ba
    assert result_ab == [("email", "email_addr"), ("mail2", "mail2_new")]


def test_rename_prefers_higher_confidence_partner():
    """'email' must pair with the more similar 'email_address', leaving
    'e_mail' for the positional partner - a greedy first-come pairing on
    unsorted input could get this wrong."""
    dropped = {
        "email": _col("email", ordinal=1),
        "phone": _col("phone", ordinal=2),
    }
    added = {
        "phone_number": _col("phone_number", ordinal=2),
        "email_address": _col("email_address", ordinal=1),
    }
    pairs = {(o.name, n.name) for o, n, _ in _detect_renames(dropped, added)}
    assert pairs == {("email", "email_address"), ("phone", "phone_number")}


def test_rename_lexicographic_tie_break():
    """Two identical candidates: deterministic lexicographic assignment."""
    dropped = {"col_b": _col("col_b", ordinal=5), "col_a": _col("col_a", ordinal=6)}
    added = {"col_y": _col("col_y", ordinal=5), "col_x": _col("col_x", ordinal=6)}
    result = [(o.name, n.name) for o, n, _ in _detect_renames(dropped, added)]
    # positional matches pair 5->5 and 6->6; ordering of output by old name
    assert result == [("col_a", "col_x"), ("col_b", "col_y")]


def test_rename_exact_type_and_flags_raise_confidence():
    exact = _detect_renames(
        {"amount": _col("amount", dtype="DECIMAL(19,4)", nullable=False, ordinal=3)},
        {"amount_usd": _col("amount_usd", dtype="DECIMAL(19,4)", nullable=False, ordinal=3)},
    )[0][2]
    weaker = _detect_renames(
        {"amount": _col("amount", dtype="DECIMAL(19,4)", nullable=False, ordinal=3)},
        {"amount_usd": _col("amount_usd", dtype="DECIMAL(10,2)", nullable=True, ordinal=3)},
    )[0][2]
    assert exact > weaker


def test_rename_different_base_types_never_pair():
    assert _detect_renames(
        {"a": _col("a", dtype="INTEGER")}, {"a2": _col("a2", dtype="VARCHAR")}
    ) == []


def test_vanished_layer_reports_all_tables_dropped(silver_baseline):
    """Baseline layer absent from the current snapshot must scream, not skip."""
    from fabric_drift_detective.schema_diff import diff_all

    drifts = diff_all({Layer.SILVER: silver_baseline}, {})
    assert len(drifts) == len(silver_baseline.tables)
    assert {d.drift_type for d in drifts} == {DriftType.TABLE_DROP}
    assert all(d.severity is Severity.CRITICAL for d in drifts)
    assert {d.table for d in drifts} == set(silver_baseline.tables)
