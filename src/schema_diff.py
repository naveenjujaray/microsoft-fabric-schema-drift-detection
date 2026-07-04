"""Schema drift detection engine.

Compares a baseline ``LayerSchema`` snapshot against the current one
and emits typed ``DriftRecord`` objects. Cross-layer breaks are added
afterwards by ``lineage.annotate_downstream`` using the lineage graph.

Drift types:
    column_drop, column_add, type_change, precision_scale_change,
    column_rename, column_reorder, nullability_change, table_drop,
    table_add, key_change, measure_drop, measure_add, measure_change,
    cross_layer_break
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from typing import Any

from .backends.base import ColumnSchema, Layer, LayerSchema, TableSchema


class DriftType(str, Enum):
    COLUMN_DROP = "column_drop"
    COLUMN_ADD = "column_add"
    TYPE_CHANGE = "type_change"
    PRECISION_SCALE_CHANGE = "precision_scale_change"
    COLUMN_RENAME = "column_rename"
    COLUMN_REORDER = "column_reorder"
    NULLABILITY_CHANGE = "nullability_change"
    TABLE_DROP = "table_drop"
    TABLE_ADD = "table_add"
    KEY_CHANGE = "key_change"
    MEASURE_DROP = "measure_drop"
    MEASURE_ADD = "measure_add"
    MEASURE_CHANGE = "measure_change"
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
    confidence: float | None = None  # heuristic confidence (renames), 0..1
    workspace: str | None = None  # owning workspace name, if known

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
            "confidence": self.confidence,
            "workspace": self.workspace,
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


def _type_params(dtype: str) -> tuple[int, ...]:
    """Parse precision/scale/length: 'DECIMAL(19,4)'->(19,4), 'VARCHAR(50)'->(50,).

    Returns () for unparameterized types (INTEGER) and for non-numeric
    parameters (VARCHAR(MAX)), which are treated as *unbounded* below.
    """
    lp = dtype.find("(")
    if lp == -1:
        return ()
    rp = dtype.find(")", lp)
    if rp == -1:
        return ()
    parts = [p.strip() for p in dtype[lp + 1 : rp].split(",") if p.strip()]
    out: list[int] = []
    for p in parts:
        if not p.isdigit():
            return ()  # e.g. VARCHAR(MAX) -> unbounded
        out.append(int(p))
    return tuple(out)


def _precision_delta(old_dtype: str, new_dtype: str) -> str:
    """'same' | 'widen' | 'narrow' for two same-base-type declarations.

    Empty params = unbounded/unknown. Bounded->unbounded widens capacity;
    unbounded->bounded narrows it; otherwise compare element-wise.
    """
    o, n = _type_params(old_dtype), _type_params(new_dtype)
    if o == n:
        return "same"
    if not o and n:
        return "narrow"   # unbounded -> bounded loses capacity
    if o and not n:
        return "widen"    # bounded -> unbounded gains capacity
    width = max(len(o), len(n))
    o = o + (0,) * (width - len(o))
    n = n + (0,) * (width - len(n))
    return "widen" if all(nn >= oo for oo, nn in zip(o, n)) else "narrow"


def _normalize_dax(expr: str) -> str:
    """Strip whitespace so formatting/reindent edits aren't flagged as drift.

    DAX whitespace is insignificant outside string literals, so comparing
    whitespace-free forms catches semantic changes while ignoring the TMDL
    reformatting that Power BI Desktop applies on every save.
    """
    return "".join(expr.split())


def _name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _rename_confidence(old: ColumnSchema, new: ColumnSchema) -> float:
    """Confidence (0..1) that ``old`` was renamed to ``new``.

    Weighted evidence: name similarity dominates, positional identity
    is strong, and matching type parameters / nullability / key flag
    each add supporting signal. Base types must already match.
    """
    sim = _name_similarity(old.name, new.name)
    positional = old.ordinal == new.ordinal
    exact_type = old.dtype == new.dtype  # includes precision/scale/length
    score = (
        0.55 * sim
        + (0.20 if positional else 0.0)
        + (0.10 if exact_type else 0.0)
        + (0.075 if old.nullable == new.nullable else 0.0)
        + (0.075 if old.is_key == new.is_key else 0.0)
    )
    return round(min(score, 1.0), 4)


def _detect_renames(
    dropped: dict[str, ColumnSchema],
    added: dict[str, ColumnSchema],
    threshold: float = 0.55,
) -> list[tuple[ColumnSchema, ColumnSchema, float]]:
    """Pair dropped+added columns that are probably renames.

    Eligibility: identical base type AND (same ordinal position OR name
    similarity >= ``threshold``) - same heuristics as before, but the
    pairing itself is now a deterministic *stable matching*
    (Gale-Shapley) over confidence scores:

    * both sides rank partners by confidence, ties broken
      lexicographically by column name - so the result never depends
      on dict insertion order;
    * the matching is stable: no dropped/added pair both prefer each
      other over their assigned partners;
    * each returned pair carries its confidence score.

    Returns ``(old, new, confidence)`` tuples ordered by old name.
    """
    # eligible pair -> confidence
    scores: dict[tuple[str, str], float] = {}
    for old in dropped.values():
        for new in added.values():
            if _base_type(old.dtype) != _base_type(new.dtype):
                continue
            if (
                _name_similarity(old.name, new.name) >= threshold
                or old.ordinal == new.ordinal
            ):
                scores[(old.name, new.name)] = _rename_confidence(old, new)
    if not scores:
        return []

    # deterministic preference lists (higher confidence first, then name)
    old_names = sorted({o for o, _ in scores})
    new_names = sorted({n for _, n in scores})
    old_prefs = {
        o: sorted(
            (n for n in new_names if (o, n) in scores),
            key=lambda n: (-scores[(o, n)], n),
        )
        for o in old_names
    }
    new_rank = {
        n: {
            o: rank
            for rank, o in enumerate(
                sorted(
                    (o for o in old_names if (o, n) in scores),
                    key=lambda o: (-scores[(o, n)], o),
                )
            )
        }
        for n in new_names
    }

    # Gale-Shapley: dropped columns propose in lexicographic order
    engaged: dict[str, str] = {}  # new -> old
    next_choice = {o: 0 for o in old_names}
    free = deque(old_names)
    while free:
        o = free.popleft()
        prefs = old_prefs[o]
        if next_choice[o] >= len(prefs):
            continue  # exhausted all acceptable partners
        n = prefs[next_choice[o]]
        next_choice[o] += 1
        current = engaged.get(n)
        if current is None:
            engaged[n] = o
        elif new_rank[n][o] < new_rank[n][current]:
            engaged[n] = o
            free.append(current)
        else:
            free.append(o)

    return [
        (dropped[o], added[n], scores[(o, n)])
        for n, o in sorted(engaged.items(), key=lambda kv: kv[1])
    ]


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
    for old_col, new_col, confidence in _detect_renames(dropped, added):
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
                confidence=confidence,
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

    # shared columns: type / precision / nullability / key changes
    shared = base_cols.keys() & cur_cols.keys()
    for name in shared:
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
        elif b.dtype != c.dtype:
            # same base type, different precision/scale/length (money truncation,
            # VARCHAR shrink...). Widening is safe; narrowing risks data loss.
            delta = _precision_delta(b.dtype, c.dtype)
            if delta != "same":
                widen = delta == "widen"
                drifts.append(
                    DriftRecord(
                        layer=layer,
                        drift_type=DriftType.PRECISION_SCALE_CHANGE,
                        severity=Severity.WARNING if widen else Severity.CRITICAL,
                        table=base.name,
                        column=name,
                        old=b.dtype,
                        new=c.dtype,
                        auto_fixable=widen,
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

    # column reorder: rank *shared* columns by position in each snapshot so
    # adds/drops shifting absolute ordinals don't create false positives.
    # Breaks positional consumers (SELECT *, positional CSV/parquet binding).
    base_rank = {n: i for i, n in enumerate(sorted(shared, key=lambda x: base_cols[x].ordinal))}
    cur_rank = {n: i for i, n in enumerate(sorted(shared, key=lambda x: cur_cols[x].ordinal))}
    for name in shared:
        if base_rank[name] != cur_rank[name]:
            drifts.append(
                DriftRecord(
                    layer=layer,
                    drift_type=DriftType.COLUMN_REORDER,
                    severity=Severity.WARNING,
                    table=base.name,
                    column=name,
                    old=base_rank[name],
                    new=cur_rank[name],
                    auto_fixable=True,
                )
            )

    drifts.extend(_diff_measures(layer, base, cur))
    return drifts


def _diff_measures(
    layer: Layer, base: TableSchema, cur: TableSchema
) -> list[DriftRecord]:
    """Diff DAX measures on a semantic-model table.

    A dropped measure breaks every report visual bound to it; a changed
    expression silently shifts the numbers a business already trusts.
    """
    drifts: list[DriftRecord] = []
    base_m, cur_m = base.measures, cur.measures
    for mname in base_m.keys() - cur_m.keys():
        drifts.append(
            DriftRecord(
                layer=layer, drift_type=DriftType.MEASURE_DROP,
                severity=Severity.CRITICAL, table=base.name, column=mname,
                old=base_m[mname], new=None, auto_fixable=False,
            )
        )
    for mname in cur_m.keys() - base_m.keys():
        drifts.append(
            DriftRecord(
                layer=layer, drift_type=DriftType.MEASURE_ADD,
                severity=Severity.INFO, table=base.name, column=mname,
                old=None, new=cur_m[mname], auto_fixable=True,
            )
        )
    for mname in base_m.keys() & cur_m.keys():
        if _normalize_dax(base_m[mname]) != _normalize_dax(cur_m[mname]):
            drifts.append(
                DriftRecord(
                    layer=layer, drift_type=DriftType.MEASURE_CHANGE,
                    severity=Severity.WARNING, table=base.name, column=mname,
                    old=base_m[mname], new=cur_m[mname], auto_fixable=False,
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
