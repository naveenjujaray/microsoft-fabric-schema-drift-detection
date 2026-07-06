"""Shared fixtures."""

from __future__ import annotations

import pytest

from fabric_drift_detective.backends.base import (
    ColumnSchema,
    Layer,
    LayerSchema,
    TableSchema,
)


def make_table(name: str, cols: list[tuple[str, str, bool, bool]]) -> TableSchema:
    """cols: (name, dtype, nullable, is_key)."""
    table = TableSchema(name=name)
    for i, (cname, dtype, nullable, is_key) in enumerate(cols):
        table.columns[cname] = ColumnSchema(
            name=cname, dtype=dtype, nullable=nullable, ordinal=i, is_key=is_key
        )
    return table


@pytest.fixture
def silver_baseline() -> LayerSchema:
    return LayerSchema(
        layer=Layer.SILVER,
        tables={
            "customers": make_table(
                "customers",
                [
                    ("customer_id", "INTEGER", False, True),
                    ("email", "VARCHAR", True, False),
                    ("phone", "VARCHAR", True, False),
                ],
            ),
            "orders": make_table(
                "orders",
                [
                    ("order_id", "INTEGER", False, True),
                    ("freight", "DECIMAL(19,4)", True, False),
                    ("total", "DECIMAL(19,4)", False, False),
                ],
            ),
        },
    )
