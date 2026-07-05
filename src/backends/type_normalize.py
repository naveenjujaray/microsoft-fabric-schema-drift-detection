"""Cross-source type normalization.

The drift engine compares ``dtype`` STRINGS. Left raw, the same logical
column read from two systems (``NVARCHAR`` in SQL Server, ``STRING`` in
Snowflake, ``VARCHAR`` in Postgres) would register as a type_change —
false drift. Every source backend therefore normalizes its dialect to
one canonical vocabulary BEFORE schemas reach the diff:

    string, int, bigint, decimal, float, bool, timestamp, date, binary

Type parameters are preserved (``NVARCHAR(50)`` -> ``string(50)``,
``DECIMAL(19,4)`` -> ``decimal(19,4)``) because precision/scale/length
feed ``precision_scale_change`` detection.

An unmapped type passes through unchanged with a one-time warning —
unknown never means crash, and passthrough keeps same-source diffs
correct even when the map is incomplete.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: the canonical vocabulary every source maps into
CANONICAL_TYPES = frozenset({
    "string", "int", "bigint", "decimal", "float",
    "bool", "timestamp", "date", "binary",
})

#: shared baseline covering common ANSI/ISO spellings; per-source maps
#: merge their dialect's names over this.
ANSI_TYPE_MAP: dict[str, str] = {
    # strings
    "CHAR": "string",
    "NCHAR": "string",
    "VARCHAR": "string",
    "NVARCHAR": "string",
    "CHARACTER": "string",
    "CHARACTER VARYING": "string",
    "TEXT": "string",
    "CLOB": "string",
    "NCLOB": "string",
    # integers
    "TINYINT": "int",
    "SMALLINT": "int",
    "INT": "int",
    "INTEGER": "int",
    "BIGINT": "bigint",
    # exact / approximate numerics
    "DECIMAL": "decimal",
    "NUMERIC": "decimal",
    "MONEY": "decimal",
    "SMALLMONEY": "decimal",
    "REAL": "float",
    "FLOAT": "float",
    "DOUBLE": "float",
    "DOUBLE PRECISION": "float",
    # booleans
    "BOOLEAN": "bool",
    "BOOL": "bool",
    "BIT": "bool",
    # temporal
    "TIMESTAMP": "timestamp",
    "TIMESTAMP WITHOUT TIME ZONE": "timestamp",
    "TIMESTAMP WITH TIME ZONE": "timestamp",
    "DATETIME": "timestamp",
    "DATETIME2": "timestamp",
    "SMALLDATETIME": "timestamp",
    "DATE": "date",
    # binary
    "BINARY": "binary",
    "VARBINARY": "binary",
    "BLOB": "binary",
    "BYTEA": "binary",
}


class TypeNormalizer:
    """Maps one source dialect's type names to the canonical vocabulary."""

    def __init__(self, type_map: dict[str, str], source: str = "source") -> None:
        bad = {v for v in type_map.values()} - CANONICAL_TYPES
        if bad:
            raise ValueError(
                f"type map for {source!r} contains non-canonical target(s) "
                f"{sorted(bad)}; allowed: {sorted(CANONICAL_TYPES)}"
            )
        self.type_map = {k.upper(): v for k, v in type_map.items()}
        self.source = source
        self._warned: set[str] = set()

    def normalize(self, dtype: str) -> str:
        """Canonicalize one dtype string, preserving ``(...)`` parameters."""
        raw = dtype.strip()
        lp = raw.find("(")
        base = (raw[:lp] if lp != -1 else raw).strip().upper()
        params = raw[lp:] if lp != -1 else ""
        canonical = self.type_map.get(base)
        if canonical is None:
            if base not in self._warned:
                self._warned.add(base)
                logger.warning(
                    "unmapped %s type %r - passing through unnormalized "
                    "(extend the type map to avoid cross-source false drift)",
                    self.source, base,
                )
            return raw
        return f"{canonical}{params}"
