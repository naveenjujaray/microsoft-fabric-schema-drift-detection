"""Load AdventureWorksLT sample tables into the local DuckDB Bronze schema.

Generates a deterministic, self-contained subset of AdventureWorksLT
(Customer, ProductCategory, Product, SalesOrderHeader,
SalesOrderDetail) so the demo needs no network access or Fabric
capacity. Column names and types mirror the real AdventureWorksLT
schema so the medallion transforms are realistic.

Usage:
    python -m sample_data.load_adventureworks [--db sample_data/warehouse.duckdb]
"""

from __future__ import annotations

import argparse
import random
from datetime import date, timedelta
from pathlib import Path

import duckdb

DEFAULT_DB = Path(__file__).parent / "warehouse.duckdb"

_FIRST = ["Orlando", "Keith", "Donna", "Janet", "Lucy", "Rosmarie", "Dominic",
          "Kwai", "Christopher", "Andrea", "Jane", "Larry", "Paul", "Wanda"]
_LAST = ["Gee", "Harris", "Carreras", "Gates", "Harrington", "Carroll",
         "Gash", "Lee", "Beck", "Thompson", "Doe", "Gigi", "West", "Vega"]
_COMPANIES = ["A Bike Store", "Progressive Sports", "Advanced Bike Components",
              "Modular Cycle Systems", "Metropolitan Sports Supply",
              "Aerobic Exercise Company", "Associated Bikes"]
_CATEGORIES = ["Mountain Bikes", "Road Bikes", "Touring Bikes", "Handlebars",
               "Bottom Brackets", "Brakes", "Chains", "Cranksets"]
_COLORS = ["Red", "Black", "White", "Blue", "Silver", "Yellow", None]


def _ddl(con: duckdb.DuckDBPyConnection) -> None:
    """Bronze tables mirroring AdventureWorksLT (SalesLT) column contracts."""
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
    con.execute("""
        CREATE OR REPLACE TABLE bronze.Customer (
            CustomerID INTEGER PRIMARY KEY,
            FirstName VARCHAR NOT NULL,
            LastName VARCHAR NOT NULL,
            CompanyName VARCHAR,
            EmailAddress VARCHAR,
            Phone VARCHAR
        )""")
    con.execute("""
        CREATE OR REPLACE TABLE bronze.ProductCategory (
            ProductCategoryID INTEGER PRIMARY KEY,
            Name VARCHAR NOT NULL
        )""")
    con.execute("""
        CREATE OR REPLACE TABLE bronze.Product (
            ProductID INTEGER PRIMARY KEY,
            Name VARCHAR NOT NULL,
            ProductNumber VARCHAR NOT NULL,
            Color VARCHAR,
            StandardCost DECIMAL(19,4) NOT NULL,
            ListPrice DECIMAL(19,4) NOT NULL,
            ProductCategoryID INTEGER
        )""")
    con.execute("""
        CREATE OR REPLACE TABLE bronze.SalesOrderHeader (
            SalesOrderID INTEGER PRIMARY KEY,
            OrderDate DATE NOT NULL,
            DueDate DATE NOT NULL,
            ShipDate DATE,
            CustomerID INTEGER NOT NULL,
            SubTotal DECIMAL(19,4) NOT NULL,
            TaxAmt DECIMAL(19,4) NOT NULL,
            Freight DECIMAL(19,4) NOT NULL,
            TotalDue DECIMAL(19,4) NOT NULL
        )""")
    con.execute("""
        CREATE OR REPLACE TABLE bronze.SalesOrderDetail (
            SalesOrderID INTEGER NOT NULL,
            SalesOrderDetailID INTEGER PRIMARY KEY,
            OrderQty SMALLINT NOT NULL,
            ProductID INTEGER NOT NULL,
            UnitPrice DECIMAL(19,4) NOT NULL,
            LineTotal DECIMAL(38,6) NOT NULL
        )""")


def _rows(con: duckdb.DuckDBPyConnection, seed: int = 42) -> None:
    rng = random.Random(seed)

    customers = [
        (
            i,
            rng.choice(_FIRST),
            rng.choice(_LAST),
            rng.choice(_COMPANIES),
            f"customer{i}@adventure-works.com",
            f"{rng.randint(100, 999)}-555-{rng.randint(1000, 9999)}",
        )
        for i in range(1, 51)
    ]
    con.executemany(
        "INSERT INTO bronze.Customer VALUES (?, ?, ?, ?, ?, ?)", customers
    )

    categories = [(i + 1, name) for i, name in enumerate(_CATEGORIES)]
    con.executemany("INSERT INTO bronze.ProductCategory VALUES (?, ?)", categories)

    products = []
    for i in range(1, 41):
        cost = round(rng.uniform(20, 1500), 4)
        products.append(
            (
                i,
                f"Product {i:03d}",
                f"PR-{i:04d}",
                rng.choice(_COLORS),
                cost,
                round(cost * rng.uniform(1.3, 2.2), 4),
                rng.randint(1, len(_CATEGORIES)),
            )
        )
    con.executemany(
        "INSERT INTO bronze.Product VALUES (?, ?, ?, ?, ?, ?, ?)", products
    )

    headers = []
    details = []
    detail_id = 1
    start = date(2024, 1, 1)
    for order_id in range(1, 121):
        order_date = start + timedelta(days=rng.randint(0, 540))
        subtotal = 0.0
        for _ in range(rng.randint(1, 4)):
            product = rng.choice(products)
            qty = rng.randint(1, 5)
            unit_price = float(product[5])
            line_total = round(qty * unit_price, 6)
            subtotal += line_total
            details.append(
                (order_id, detail_id, qty, product[0], unit_price, line_total)
            )
            detail_id += 1
        tax = round(subtotal * 0.08, 4)
        freight = round(subtotal * 0.025, 4)
        headers.append(
            (
                order_id,
                order_date,
                order_date + timedelta(days=12),
                order_date + timedelta(days=rng.randint(2, 8)),
                rng.randint(1, 50),
                round(subtotal, 4),
                tax,
                freight,
                round(subtotal + tax + freight, 4),
            )
        )
    con.executemany(
        "INSERT INTO bronze.SalesOrderHeader VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        headers,
    )
    con.executemany(
        "INSERT INTO bronze.SalesOrderDetail VALUES (?, ?, ?, ?, ?, ?)", details
    )


def load(db_path: str | Path = DEFAULT_DB, seed: int = 42) -> Path:
    """(Re)create the Bronze layer with AdventureWorksLT sample data."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        _ddl(con)
        _rows(con, seed)
    finally:
        con.close()
    return db_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()
    path = load(args.db)
    print(f"Bronze layer loaded into {path}")
