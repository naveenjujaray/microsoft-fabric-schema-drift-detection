"""Lineage manifest: Bronze->Silver->Gold column mappings as data, not code.

The demo constants in ``medallion.py`` describe AdventureWorksLT. Real
estates declare their own transformation contract in a small YAML/JSON
manifest and point ``lineage.manifest`` (config.yaml) at it — the graph
builder consumes the same ``(src_table, src_column, dst_table,
dst_column)`` tuples either way.

Manifest shape (see ``examples/lineage.example.yaml``)::

    bronze_to_silver:
      - [Customer, CustomerID, customers, customer_id]     # positional
      - src_table: Customer                                # or named
        src_column: EmailAddress
        dst_table: customers
        dst_column: email
    silver_to_gold:
      - [customers, customer_id, Dim_Customer, CustomerKey]

Both sections are optional; entries may mix positional (4-item list)
and named (4-key mapping) forms. Validation errors always name the
offending section and index.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_SECTIONS = ("bronze_to_silver", "silver_to_gold")
_KEYS = ("src_table", "src_column", "dst_table", "dst_column")


class LineageManifestError(ValueError):
    """The lineage manifest is missing or malformed."""


@dataclass(frozen=True)
class ColumnMapping:
    """One column-level edge of the transformation contract."""

    src_table: str
    src_column: str
    dst_table: str
    dst_column: str

    def as_tuple(self) -> tuple[str, str, str, str]:
        return (self.src_table, self.src_column, self.dst_table, self.dst_column)


def _parse_entry(section: str, index: int, entry: Any) -> ColumnMapping:
    where = f"{section}[{index}]"
    if isinstance(entry, (list, tuple)):
        values = list(entry)
        if len(values) != 4:
            raise LineageManifestError(
                f"{where}: expected 4 items "
                "(src_table, src_column, dst_table, dst_column), "
                f"got {len(values)}: {entry!r}"
            )
    elif isinstance(entry, dict):
        missing = [k for k in _KEYS if k not in entry]
        if missing:
            raise LineageManifestError(
                f"{where}: missing key(s) {missing}: {entry!r}"
            )
        extra = set(entry) - set(_KEYS)
        if extra:
            raise LineageManifestError(
                f"{where}: unknown key(s) {sorted(extra)}: {entry!r}"
            )
        values = [entry[k] for k in _KEYS]
    else:
        raise LineageManifestError(
            f"{where}: expected a 4-item list or a mapping, got {entry!r}"
        )

    cleaned: list[str] = []
    for key, value in zip(_KEYS, values, strict=True):
        if not isinstance(value, str) or not value.strip():
            raise LineageManifestError(
                f"{where}: {key} must be a non-empty string, got {value!r}"
            )
        cleaned.append(value.strip())
    return ColumnMapping(*cleaned)


@dataclass
class LineageManifest:
    """Parsed transformation contract for the medallion layers."""

    bronze_to_silver: list[ColumnMapping] = field(default_factory=list)
    silver_to_gold: list[ColumnMapping] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> LineageManifest:
        if not isinstance(data, dict):
            raise LineageManifestError(
                "manifest top level must be a mapping with "
                f"section(s) {list(_SECTIONS)}"
            )
        unknown = set(data) - set(_SECTIONS)
        if unknown:
            raise LineageManifestError(
                f"unknown manifest section(s) {sorted(unknown)}; "
                f"expected only {list(_SECTIONS)}"
            )
        sections: dict[str, list[ColumnMapping]] = {}
        for section in _SECTIONS:
            entries = data.get(section) or []
            if not isinstance(entries, list):
                raise LineageManifestError(
                    f"{section}: expected a list of entries, got {entries!r}"
                )
            sections[section] = [
                _parse_entry(section, i, entry)
                for i, entry in enumerate(entries)
            ]
        return cls(
            bronze_to_silver=sections["bronze_to_silver"],
            silver_to_gold=sections["silver_to_gold"],
        )

    @classmethod
    def load(cls, path: str | Path) -> LineageManifest:
        """Load a YAML or JSON manifest file.

        Raises ``LineageManifestError`` (not a raw OSError traceback)
        naming the path when the file is missing or unreadable.
        """
        path = Path(path)
        if not path.exists():
            raise LineageManifestError(
                f"lineage manifest not found: {path} "
                "(check lineage.manifest in config.yaml)"
            )
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise LineageManifestError(
                f"cannot parse lineage manifest {path}: {exc}"
            ) from exc
        try:
            return cls.from_dict(data)
        except LineageManifestError as exc:
            raise LineageManifestError(f"{path}: {exc}") from exc


def load_lineage_manifest(path: str | Path | None) -> LineageManifest | None:
    """Config-level helper: None/empty path -> None (use demo defaults)."""
    if not path:
        return None
    return LineageManifest.load(path)
