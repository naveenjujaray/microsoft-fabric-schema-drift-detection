"""Cross-layer lineage graph: Bronze -> Silver -> Gold -> Semantic Model -> Reports.

This is the differentiator: when a column changes in Silver, we can
walk the graph and answer exactly which Gold columns, DAX measures and
PBIP report fields are now at risk — then emit ``cross_layer_break``
drift records for them.

Node id format: ``"<layer>:<table>.<column>"`` for columns,
``"<layer>:<table>#<measure>"`` for measures, and
``"reports:<report>.<binding>"`` for report field bindings.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from typing import Iterable

from .backends.base import Layer, LayerSchema
from .schema_diff import DriftRecord, DriftType, Severity

_COLUMN_REF = re.compile(r"'?(\w+)'?\[(\w+)\]")  # DAX Table[Column] refs


def node_id(layer: Layer, table: str, column: str | None = None,
            measure: str | None = None) -> str:
    """Canonical node id."""
    if measure is not None:
        return f"{layer.value}:{table}#{measure}"
    if column is not None:
        return f"{layer.value}:{table}.{column}"
    return f"{layer.value}:{table}"


class LineageGraph:
    """Directed graph of column-level lineage across layers."""

    def __init__(self) -> None:
        self._out: dict[str, set[str]] = defaultdict(set)
        self._in: dict[str, set[str]] = defaultdict(set)

    # ------------------------------------------------------------------
    def add_edge(self, upstream: str, downstream: str) -> None:
        self._out[upstream].add(downstream)
        self._in[downstream].add(upstream)

    def add_mapping(
        self,
        src_layer: Layer,
        src_table: str,
        src_column: str,
        dst_layer: Layer,
        dst_table: str,
        dst_column: str,
    ) -> None:
        self.add_edge(
            node_id(src_layer, src_table, src_column),
            node_id(dst_layer, dst_table, dst_column),
        )

    # ------------------------------------------------------------------
    def downstream(self, node: str) -> list[str]:
        """Every node transitively downstream of ``node`` (BFS order)."""
        seen: set[str] = set()
        queue: deque[str] = deque(self._out.get(node, ()))
        result: list[str] = []
        while queue:
            n = queue.popleft()
            if n in seen:
                continue
            seen.add(n)
            result.append(n)
            queue.extend(self._out.get(n, ()))
        return result

    def upstream(self, node: str) -> list[str]:
        """Every node transitively upstream of ``node``."""
        seen: set[str] = set()
        queue: deque[str] = deque(self._in.get(node, ()))
        result: list[str] = []
        while queue:
            n = queue.popleft()
            if n in seen:
                continue
            seen.add(n)
            result.append(n)
            queue.extend(self._in.get(n, ()))
        return result

    @property
    def edge_count(self) -> int:
        return sum(len(v) for v in self._out.values())

    def edges(self) -> Iterable[tuple[str, str]]:
        for src, dsts in self._out.items():
            for dst in dsts:
                yield src, dst

    # ------------------------------------------------------------------
    def register_semantic_model(self, model: LayerSchema) -> None:
        """Wire Gold columns -> semantic-model columns and measures.

        * A model table with ``metadata['source_table']`` maps each of
          its columns 1:1 from the Gold table of that name.
        * Each DAX measure gets edges from every ``Table[Column]``
          reference in its expression (semantic-model columns), so a
          Gold drop propagates: gold col -> model col -> measure.
        """
        for table in model.tables.values():
            source = table.metadata.get("source_table", "")
            for col_name in table.columns:
                if source:
                    self.add_edge(
                        node_id(Layer.GOLD, source, col_name),
                        node_id(Layer.SEMANTIC_MODEL, table.name, col_name),
                    )
            for measure_name, dax in table.measures.items():
                measure_node = node_id(
                    Layer.SEMANTIC_MODEL, table.name, measure=measure_name
                )
                for ref_table, ref_col in _COLUMN_REF.findall(dax):
                    self.add_edge(
                        node_id(Layer.SEMANTIC_MODEL, ref_table, ref_col),
                        measure_node,
                    )

    def register_reports(self, reports: LayerSchema) -> None:
        """Wire semantic-model columns/measures -> report field bindings.

        Report 'columns' are named ``ModelTable.FieldOrMeasure``; kind
        metadata distinguishes measures from columns.
        """
        for report in reports.tables.values():
            for binding_name, col in report.columns.items():
                model_table, _, fieldname = binding_name.partition(".")
                if col.dtype.upper() == "MEASURE":
                    src = node_id(
                        Layer.SEMANTIC_MODEL, model_table, measure=fieldname
                    )
                else:
                    src = node_id(Layer.SEMANTIC_MODEL, model_table, fieldname)
                self.add_edge(
                    src, node_id(Layer.REPORTS, report.name, binding_name)
                )


# ----------------------------------------------------------------------
_BREAKING = {
    DriftType.COLUMN_DROP,
    DriftType.COLUMN_RENAME,
    DriftType.TYPE_CHANGE,
    DriftType.PRECISION_SCALE_CHANGE,
    DriftType.KEY_CHANGE,
    DriftType.TABLE_DROP,
    DriftType.MEASURE_DROP,
}


def annotate_downstream(
    drifts: list[DriftRecord], graph: LineageGraph
) -> list[DriftRecord]:
    """Fill ``downstream_impact`` on each drift and synthesize
    ``cross_layer_break`` records for impacted nodes in *other* layers.

    Returns the combined list (original drifts + synthesized breaks).
    """
    extra: list[DriftRecord] = []
    seen_breaks: set[str] = set()

    for drift in drifts:
        if drift.column:
            node = node_id(drift.layer, drift.table, drift.column)
        else:
            node = node_id(drift.layer, drift.table)
        impacted = graph.downstream(node)

        # table-level drifts (table_drop) impact all downstream of each column;
        # approximate by scanning all nodes with the table prefix
        if not impacted and drift.drift_type is DriftType.TABLE_DROP:
            prefix = f"{drift.layer.value}:{drift.table}."
            for src, _ in list(graph.edges()):
                if src.startswith(prefix):
                    impacted.extend(graph.downstream(src))
            impacted = list(dict.fromkeys(impacted))

        drift.downstream_impact = impacted

        if drift.drift_type not in _BREAKING or drift.severity is Severity.INFO:
            continue

        for target in impacted:
            layer_name = target.split(":", 1)[0]
            if layer_name == drift.layer.value:
                continue  # same-layer propagation isn't a *cross*-layer break
            if target in seen_breaks:
                continue
            seen_breaks.add(target)

            tgt_layer = Layer(layer_name)
            rest = target.split(":", 1)[1]
            if "#" in rest:
                table, column = rest.split("#", 1)
                column = f"[measure] {column}"
            elif "." in rest:
                table, column = rest.split(".", 1)
            else:
                table, column = rest, None
            extra.append(
                DriftRecord(
                    layer=tgt_layer,
                    drift_type=DriftType.CROSS_LAYER_BREAK,
                    severity=Severity.CRITICAL,
                    table=table,
                    column=column,
                    old=f"depends on {node}",
                    new=f"broken by {drift.drift_type.value}",
                    downstream_impact=graph.downstream(target),
                    auto_fixable=drift.drift_type is DriftType.COLUMN_RENAME,
                )
            )
    return drifts + extra
