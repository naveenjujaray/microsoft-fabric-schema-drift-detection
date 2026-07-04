"""Deliberately mutate the medallion schema to demo drift detection.

Scenarios (pick with --scenario, default 'all'):
    rename       silver.customers.email -> email_address (cross-layer break:
                 Gold Dim_Customer.Email, model Customer[Email], report bindings)
    drop         silver.sales_orders.freight dropped (breaks Fact_Sales.Freight
                 and the 'Revenue incl Freight' measure)
    type_change  silver.sales_order_lines.unit_price DECIMAL -> VARCHAR
                 (breaks Avg Unit Price measure)
    nullability  bronze.Product.StandardCost NOT NULL -> nullable
    precision    silver.sales_orders.subtotal DECIMAL(19,4) -> DECIMAL(10,2)
                 (money truncation risk; critical narrowing)
    add          bronze.Customer gains LoyaltyTier column (info-level)

Usage:
    python -m sample_data.inject_drift --scenario all
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

DEFAULT_DB = Path(__file__).parent / "warehouse.duckdb"


def rename_email(con: duckdb.DuckDBPyConnection) -> str:
    con.execute("ALTER TABLE silver.customers RENAME COLUMN email TO email_address")
    return "silver.customers: email -> email_address (rename)"


def drop_freight(con: duckdb.DuckDBPyConnection) -> str:
    con.execute("ALTER TABLE silver.sales_orders DROP COLUMN freight")
    return "silver.sales_orders: freight dropped"


def retype_unit_price(con: duckdb.DuckDBPyConnection) -> str:
    con.execute("""
        ALTER TABLE silver.sales_order_lines
        ALTER COLUMN unit_price SET DATA TYPE VARCHAR
    """)
    return "silver.sales_order_lines: unit_price DECIMAL -> VARCHAR"


def relax_standard_cost(con: duckdb.DuckDBPyConnection) -> str:
    con.execute("ALTER TABLE bronze.Product ALTER COLUMN StandardCost DROP NOT NULL")
    return "bronze.Product: StandardCost NOT NULL -> nullable"


def narrow_subtotal(con: duckdb.DuckDBPyConnection) -> str:
    con.execute("""
        ALTER TABLE silver.sales_orders
        ALTER COLUMN subtotal SET DATA TYPE DECIMAL(10,2)
    """)
    return "silver.sales_orders: subtotal DECIMAL(19,4) -> DECIMAL(10,2)"


def add_loyalty(con: duckdb.DuckDBPyConnection) -> str:
    con.execute("ALTER TABLE bronze.Customer ADD COLUMN LoyaltyTier VARCHAR")
    return "bronze.Customer: LoyaltyTier added"


SCENARIOS = {
    "rename": rename_email,
    "drop": drop_freight,
    "type_change": retype_unit_price,
    "nullability": relax_standard_cost,
    "precision": narrow_subtotal,
    "add": add_loyalty,
}


def inject(db_path: str | Path = DEFAULT_DB, scenario: str = "all") -> list[str]:
    """Apply one or all drift scenarios; returns descriptions applied."""
    names = list(SCENARIOS) if scenario == "all" else [scenario]
    applied: list[str] = []
    con = duckdb.connect(str(db_path))
    try:
        for name in names:
            applied.append(SCENARIOS[name](con))
    finally:
        con.close()
    return applied


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument(
        "--scenario", default="all", choices=["all", *SCENARIOS.keys()]
    )
    args = parser.parse_args()
    for line in inject(args.db, args.scenario):
        print(f"injected: {line}")
