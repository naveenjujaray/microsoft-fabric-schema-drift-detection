"""Schema drift detection engine.

Compares a baseline ``LayerSchema`` snapshot against the current one
and emits typed ``DriftRecord`` objects. Cross-layer breaks are added
afterwards by ``lineage.annotate_downstream`` using the lineage graph.

Drift types:
    column_drop, column_add, type_change, column_rename,
    nullability_change, table_drop, table_add, key_change,
    cross_layer_break
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from typing import Any

from .backends.base import ColumnSchema, Layer, LayerSchema, TableSchema


class DriftType(str, Enum):
    COLUMN_DROP = "column_drop"
    COLUMN_ADD = "column_add"
    TYPE_CHANGE = "type_change"
    COLUMN_RENAME = "column_rename"
    NULLABILITY_CHANGE = "nullability_change"
    TABLE_DROP = "table_drop"
    TABLE_ADD = "table_add"
    KEY_CHANGE = "key_change"
    CROSS_LAYER_BREAK = "cross_layer_break"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# types whose loss of precision is safe when cast in this direction
_SAFE_CASTS: set[tuple[str, str]] = {
    ("INTEGER", "BIGINT"),
    ("SMALLINT", "INTEGER"),
    ("SMALLINT", "BIGINT"),
    ("TINYINT", "SMALLINT"),
    ("TINYINT", "INTEGER"),
    ("TINYINT", "BIGINT"),
    ("FLOAT", "DOUBLE"),
    ("REAL", "DOUBLE"),
    ("INTEGER", "DECIMAL"),
    ("BIGINT", "DECIMAL"),
    ("VARCHAR", "TEXT"),
}


@dataclass
class DriftRecord:
    """One detected drift."""

    layer: Layer
    drift_type: DriftType
    severity: Severity
    table: str
    column: str | None = None
    old: Any = None
    new: Any = None
    downstream_impact: list[str] = field(default_factory=list)
    auto_fixable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer.value,
            "drift_type": self.drift_type.value,
            "severity": self.severity.value,
            "table": self.table,
            "column": self.column,
            "old": self.old,
            "new": self.new,
            "downstream_impact": list(self.downstream_impact),
            "auto_fixable": self.auto_fixable,
        }

    def describe(self) -> str:
        """One-line human description."""
        col = f".{self.column}" if self.column else ""
        return (
            f"[{self.severity.value.upper()}] {self.layer.value}:"
            f"{self.table}{col} {self.drift_type.value}"
            f" ({self.old!r} -> {self.new!r})"
        )


def _base_type(dtype: str) -> str:
    """Normalize 'DECIMAL(10,2)' -> 'DECIMAL' etc."""
    return dtype.split("(")[0].strip().upper()


def _cast_is_safe(old: str, new: str) -> bool:
    o, n = _base_type(old), _base_type(new)
    if o == n:
        return True
    return (o, n) in _SAFE_CASTS


def _name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _detect_renames(
    dropped: dict[str, ColumnSchema],
    added: dict[str, ColumnSchema],
    threshold: float = 0.55,
) -> list[tuple[ColumnSchema, ColumnSchema]]:
    """Pair dropped+added columns that are probably renames.

    Heuristics: identical type AND (same ordinal position OR name
    similarity above threshold). Each column used at most once,
    best-scoring pairs first.
    """
    candidates: list[tuple[float, ColumnSchema, ColumnSchema]] = []
    for old in dropped.values():
        for new in added.values():
            if _base_type(old.dtype) != _base_type(new.dtype):
                continue
            sim = _name_similarity(old.name, new.name)
            positional = old.ordinal == new.ordinal
            if sim >= threshold or positional:
                score = sim + (0.5 if positional else 0.0)
                candidates.append((score, old, new))
    candidates.sort(key=lambda c: c[0], reverse=True)

    pairs: list[tuple[ColumnSchema, ColumnSchema]] = []
    used_old: set[str] = set()
    used_new: set[str] = set()
    for _, old, new in candidates:
        if old.name in used_old or new.name in used_new:
            continue
        pairs.append((old, new))
        used_old.add(old.name)
        used_new.add(new.name)
    return pairs


def _diff_table(
    layer: Layer, base: TableSchema, cur: TableSchema
) -> list[DriftRecord]:
    """Diff one table's columns."""
    drifts: list[DriftRecord] = []
    base_cols = base.columns
    cur_cols = cur.columns

    dropped = {n: c for n, c in base_cols.items() if n not in cur_cols}
    added = {n: c for n, c in cur_cols.items() if n not in base_cols}

    # renames first: consume matched pairs so they aren't double-reported
    for old_col, new_col in _detect_renames(dropped, added):
        drifts.append(
            DriftRecord(
                layer=layer,
                drift_type=DriftType.COLUMN_RENAME,
                severity=Severity.CRITICAL,
                table=base.name,
                column=old_col.name,
                old=old_col.name,
                new=new_col.name,
                auto_fixable=True,  # rename is mechanically fixable downstream
            )
        )
        dropped.pop(old_col.name, None)
        added.pop(new_col.name, None)

    for col in dropped.values():
        drifts.append(
            DriftRecord(
                layer=layer,
                drift_type=DriftType.COLUMN_DROP,
                severity=Severity.CRITICAL,
                table=base.name,
                column=col.name,
                old=col.dtype,
                new=None,
                auto_fixable=False,
            )
        )
    for col in added.values():
        drifts.append(
            DriftRecord(
                layer=layer,
                drift_type=DriftType.COLUMN_ADD,
                severity=Severity.INFO,
                table=base.name,
                column=col.name,
                old=None,
                new=col.dtype,
                auto_fixable=True,
            )
        )

    # shared columns: type / nullability / key changes
    for name in base_cols.keys() & cur_cols.keys():
        b, c = base_cols[name], cur_cols[name]
        if _base_type(b.dtype) != _base_type(c.dtype):
            severity = (
                Severity.WARNING if _cast_is_safe(b.dtype, c.dtype) else Severity.CRITICAL
            )
            drifts.append(
                DriftRecord(
                    layer=layer,
                    drift_type=DriftType.TYPE_CHANGE,
                    severity=severity,
                    table=base.name,
                    column=name,
                    old=b.dtype,
                    new=c.dtype,
                    auto_fixable=_cast_is_safe(b.dtype, c.dtype),
                )
            )
        if b.nullable != c.nullable:
            drifts.append(
                DriftRecord(
                    layer=layer,
                    drift_type=DriftType.NULLABILITY_CHANGE,
                    severity=Severity.WARNING,
                    table=base.name,
                    column=name,
                    old=b.nullable,
                    new=c.nullable,
                    auto_fixable=True,
                )
            )
        if b.is_key != c.is_key:
            drifts.append(
                DriftRecord(
                    layer=layer,
                    drift_type=DriftType.KEY_CHANGE,
                    severity=Severity.CRITICAL,
                    table=base.name,
                    column=name,
                    old=b.is_key,
                    new=c.is_key,
                    auto_fixable=False,
                )
            )
    return drifts


def diff_layer(base: LayerSchema, current: LayerSchema) -> list[DriftRecord]:
    """Diff one layer's baseline snapshot against the current schema."""
    if base.layer != current.layer:
        raise ValueError("cannot diff different layers")
    layer = base.layer
    drifts: list[DriftRecord] = []

    for name in base.tables.keys() - current.tables.keys():
        drifts.append(
            DriftRecord(
                layer=layer,
                drift_type=DriftType.TABLE_DROP,
                severity=Severity.CRITICAL,
                table=name,
                old=name,
                new=None,
            )
        )
    for name in current.tables.keys() - base.tables.keys():
        drifts.append(
            DriftRecord(
                layer=layer,
                drift_type=DriftType.TABLE_ADD,
                severity=Severity.INFO,
                table=name,
                old=None,
                new=name,
                auto_fixable=True,
            )
        )
    for name in base.tables.keys() & current.tables.keys():
        drifts.extend(_diff_table(layer, base.tables[name], current.tables[name]))
    return drifts


def diff_all(
    baselines: dict[Layer, LayerSchema],
    currents: dict[Layer, LayerSchema],
) -> list[DriftRecord]:
    """Diff every layer present in both snapshots."""
    drifts: list[DriftRecord] = []
    for layer, base in baselines.items():
        cur = currents.get(layer)
        if cur is not None:
            drifts.extend(diff_layer(base, cur))
    return drifts
