"""Core schema model and the SchemaBackend ABC.

Every layer of the medallion (Bronze, Silver, Gold, Semantic Model,
Reports) is represented as a ``LayerSchema`` regardless of where it
came from — live Fabric or the local DuckDB simulation. The drift
engine only ever sees these dataclasses, which is what makes the two
modes share one code path.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Layer(str, Enum):
    """Medallion layers plus downstream consumers."""

    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    SEMANTIC_MODEL = "semantic_model"
    REPORTS = "reports"


#: sentinel for baselines snapshotted before default capture existed -
#: "not captured" must not read as "no default", or the first run after
#: an upgrade storms default_change for every column with a default
DEFAULT_NOT_CAPTURED = "__not_captured__"


@dataclass(frozen=True)
class ColumnSchema:
    """A single column's contract.

    ``default`` is the column's default expression as the catalog reports
    it (None = no default / not captured). ``flags`` are source-declared
    column attributes ("identity", "computed", "auto_increment", ...);
    both are optional — backends that don't capture them leave the
    defaults, and the differ then never fires for them.
    """

    name: str
    dtype: str
    nullable: bool = True
    ordinal: int = 0
    is_key: bool = False
    default: str | None = None
    flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dtype": self.dtype,
            "nullable": self.nullable,
            "ordinal": self.ordinal,
            "is_key": self.is_key,
            "default": self.default,
            "flags": list(self.flags),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ColumnSchema:
        return cls(
            name=d["name"],
            dtype=d["dtype"],
            nullable=d.get("nullable", True),
            ordinal=d.get("ordinal", 0),
            is_key=d.get("is_key", False),
            default=d.get("default", DEFAULT_NOT_CAPTURED),
            flags=tuple(d.get("flags", ())),
        )


@dataclass
class TableSchema:
    """A table (or semantic-model table / report dataset)."""

    name: str
    columns: dict[str, ColumnSchema] = field(default_factory=dict)
    # extra metadata: DAX measures, relationships, report fields...
    measures: dict[str, str] = field(default_factory=dict)  # name -> DAX
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "columns": {n: c.to_dict() for n, c in self.columns.items()},
            "measures": dict(self.measures),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TableSchema:
        return cls(
            name=d["name"],
            columns={
                n: ColumnSchema.from_dict(c) for n, c in d.get("columns", {}).items()
            },
            measures=dict(d.get("measures", {})),
            metadata=dict(d.get("metadata", {})),
        )


@dataclass
class LayerSchema:
    """All tables of one medallion layer."""

    layer: Layer
    tables: dict[str, TableSchema] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer.value,
            "tables": {n: t.to_dict() for n, t in self.tables.items()},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LayerSchema:
        return cls(
            layer=Layer(d["layer"]),
            tables={n: TableSchema.from_dict(t) for n, t in d.get("tables", {}).items()},
        )


class SchemaBackend(ABC):
    """Contract every backend (live Fabric, local sim) must satisfy."""

    @abstractmethod
    def get_schema(self, layer: Layer) -> LayerSchema:
        """Return the *current* schema for one medallion layer."""

    @abstractmethod
    def list_layers(self) -> list[Layer]:
        """Layers this backend can inspect."""

    def get_all_schemas(self) -> dict[Layer, LayerSchema]:
        """Snapshot every available layer."""
        return {layer: self.get_schema(layer) for layer in self.list_layers()}
