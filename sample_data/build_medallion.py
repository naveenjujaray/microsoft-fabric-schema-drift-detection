"""Build Silver and Gold layers plus semantic model / report metadata.

Bronze -> Silver: rename to snake_case, standardize types, dedupe.
Silver -> Gold:   star schema (Dim_Customer, Dim_Product, Dim_Date,
                  Fact_Sales).
Also emits ``generated/semantic_model.json`` (TMDL analogue: tables,
columns, DAX measures, relationships) and ``generated/reports.json``
(PBIP field bindings) so the lineage graph reaches all five layers.

Usage:
    python -m sample_data.build_medallion [--db sample_data/warehouse.duckdb]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

DEFAULT_DB = Path(__file__).parent / "warehouse.duckdb"
GENERATED_DIR = Path(__file__).parent / "generated"


def build_silver(con: duckdb.DuckDBPyConnection) -> None:
    """Cleaned/conformed layer: snake_case names, standardized types."""
    con.execute("CREATE SCHEMA IF NOT EXISTS silver")
    con.execute("""
        CREATE OR REPLACE TABLE silver.customers AS
        SELECT DISTINCT
            CustomerID       AS customer_id,
            FirstName        AS first_name,
            LastName         AS last_name,
            CompanyName      AS company_name,
            lower(EmailAddress) AS email,
            Phone            AS phone
        FROM bronze.Customer
    """)
    con.execute("""
        CREATE OR REPLACE TABLE silver.product_categories AS
        SELECT DISTINCT
            ProductCategoryID AS category_id,
            Name              AS category_name
        FROM bronze.ProductCategory
    """)
    con.execute("""
        CREATE OR REPLACE TABLE silver.products AS
        SELECT DISTINCT
            ProductID          AS product_id,
            Name               AS product_name,
            ProductNumber      AS product_number,
            coalesce(Color, 'N/A') AS color,
            CAST(StandardCost AS DECIMAL(19,4)) AS standard_cost,
            CAST(ListPrice AS DECIMAL(19,4))    AS list_price,
            ProductCategoryID  AS category_id
        FROM bronze.Product
    """)
    con.execute("""
        CREATE OR REPLACE TABLE silver.sales_orders AS
        SELECT DISTINCT
            SalesOrderID AS order_id,
            OrderDate    AS order_date,
            DueDate      AS due_date,
            ShipDate     AS ship_date,
            CustomerID   AS customer_id,
            CAST(SubTotal AS DECIMAL(19,4)) AS subtotal,
            CAST(TaxAmt AS DECIMAL(19,4))   AS tax_amount,
            CAST(Freight AS DECIMAL(19,4))  AS freight,
            CAST(TotalDue AS DECIMAL(19,4)) AS total_due
        FROM bronze.SalesOrderHeader
    """)
    con.execute("""
        CREATE OR REPLACE TABLE silver.sales_order_lines AS
        SELECT DISTINCT
            SalesOrderID       AS order_id,
            SalesOrderDetailID AS order_line_id,
            ProductID          AS product_id,
            CAST(OrderQty AS INTEGER)      AS quantity,
            CAST(UnitPrice AS DECIMAL(19,4)) AS unit_price,
            CAST(LineTotal AS DECIMAL(19,4)) AS line_total
        FROM bronze.SalesOrderDetail
    """)


def build_gold(con: duckdb.DuckDBPyConnection) -> None:
    """Star schema consumed by the semantic model."""
    con.execute("CREATE SCHEMA IF NOT EXISTS gold")
    con.execute("""
        CREATE OR REPLACE TABLE gold.Dim_Customer AS
        SELECT
            customer_id  AS CustomerKey,
            first_name   AS FirstName,
            last_name    AS LastName,
            company_name AS CompanyName,
            email        AS Email
        FROM silver.customers
    """)
    con.execute("""
        CREATE OR REPLACE TABLE gold.Dim_Product AS
        SELECT
            p.product_id     AS ProductKey,
            p.product_name   AS ProductName,
            p.product_number AS ProductNumber,
            p.color          AS Color,
            p.standard_cost  AS StandardCost,
            p.list_price     AS ListPrice,
            c.category_name  AS CategoryName
        FROM silver.products p
        LEFT JOIN silver.product_categories c USING (category_id)
    """)
    con.execute("""
        CREATE OR REPLACE TABLE gold.Dim_Date AS
        SELECT DISTINCT
            order_date                       AS DateKey,
            EXTRACT(year FROM order_date)    AS Year,
            EXTRACT(quarter FROM order_date) AS Quarter,
            EXTRACT(month FROM order_date)   AS Month,
            strftime(order_date, '%B')       AS MonthName
        FROM silver.sales_orders
    """)
    con.execute("""
        CREATE OR REPLACE TABLE gold.Fact_Sales AS
        SELECT
            l.order_id      AS OrderID,
            l.order_line_id AS OrderLineID,
            l.product_id    AS ProductKey,
            o.customer_id   AS CustomerKey,
            o.order_date    AS DateKey,
            l.quantity      AS Quantity,
            l.unit_price    AS UnitPrice,
            l.line_total    AS LineTotal,
            o.freight       AS Freight
        FROM silver.sales_order_lines l
        JOIN silver.sales_orders o USING (order_id)
    """)


def write_semantic_model(out_dir: Path = GENERATED_DIR) -> Path:
    """Semantic model definition (TMDL analogue) over the Gold layer."""
    model = {
        "name": "SalesModel",
        "tables": [
            {
                "name": "Customer",
                "sourceTable": "Dim_Customer",
                "columns": [
                    {"name": "CustomerKey", "dataType": "int64", "isKey": True},
                    {"name": "FirstName", "dataType": "string"},
                    {"name": "LastName", "dataType": "string"},
                    {"name": "CompanyName", "dataType": "string"},
                    {"name": "Email", "dataType": "string"},
                ],
                "measures": [],
            },
            {
                "name": "Product",
                "sourceTable": "Dim_Product",
                "columns": [
                    {"name": "ProductKey", "dataType": "int64", "isKey": True},
                    {"name": "ProductName", "dataType": "string"},
                    {"name": "ProductNumber", "dataType": "string"},
                    {"name": "Color", "dataType": "string"},
                    {"name": "StandardCost", "dataType": "decimal"},
                    {"name": "ListPrice", "dataType": "decimal"},
                    {"name": "CategoryName", "dataType": "string"},
                ],
                "measures": [
                    {
                        "name": "Avg List Price",
                        "expression": "AVERAGE(Product[ListPrice])",
                    }
                ],
            },
            {
                "name": "Date",
                "sourceTable": "Dim_Date",
                "columns": [
                    {"name": "DateKey", "dataType": "dateTime", "isKey": True},
                    {"name": "Year", "dataType": "int64"},
                    {"name": "Quarter", "dataType": "int64"},
                    {"name": "Month", "dataType": "int64"},
                    {"name": "MonthName", "dataType": "string"},
                ],
                "measures": [],
            },
            {
                "name": "Sales",
                "sourceTable": "Fact_Sales",
                "columns": [
                    {"name": "OrderID", "dataType": "int64"},
                    {"name": "OrderLineID", "dataType": "int64", "isKey": True},
                    {"name": "ProductKey", "dataType": "int64"},
                    {"name": "CustomerKey", "dataType": "int64"},
                    {"name": "DateKey", "dataType": "dateTime"},
                    {"name": "Quantity", "dataType": "int64"},
                    {"name": "UnitPrice", "dataType": "decimal"},
                    {"name": "LineTotal", "dataType": "decimal"},
                    {"name": "Freight", "dataType": "decimal"},
                ],
                "measures": [
                    {
                        "name": "Total Revenue",
                        "expression": "SUM(Sales[LineTotal])",
                    },
                    {
                        "name": "Total Quantity",
                        "expression": "SUM(Sales[Quantity])",
                    },
                    {
                        "name": "Avg Unit Price",
                        "expression": "AVERAGE(Sales[UnitPrice])",
                    },
                    {
                        "name": "Revenue incl Freight",
                        "expression": "SUM(Sales[LineTotal]) + SUM(Sales[Freight])",
                    },
                ],
            },
        ],
        "relationships": [
            {"from": "Sales[ProductKey]", "to": "Product[ProductKey]"},
            {"from": "Sales[CustomerKey]", "to": "Customer[CustomerKey]"},
            {"from": "Sales[DateKey]", "to": "Date[DateKey]"},
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "semantic_model.json"
    path.write_text(json.dumps(model, indent=2), encoding="utf-8")
    return path


def write_reports(out_dir: Path = GENERATED_DIR) -> Path:
    """PBIP report field-binding metadata (which fields each visual uses)."""
    reports = {
        "reports": [
            {
                "name": "Sales Overview",
                "path": "pbip_reports/SalesOverview.Report",
                "fields": [
                    {"table": "Sales", "field": "Total Revenue", "kind": "measure"},
                    {"table": "Sales", "field": "Total Quantity", "kind": "measure"},
                    {"table": "Date", "field": "MonthName", "kind": "column"},
                    {"table": "Product", "field": "CategoryName", "kind": "column"},
                ],
            },
            {
                "name": "Customer Detail",
                "path": "pbip_reports/CustomerDetail.Report",
                "fields": [
                    {"table": "Customer", "field": "CompanyName", "kind": "column"},
                    {"table": "Customer", "field": "Email", "kind": "column"},
                    {"table": "Sales", "field": "Total Revenue", "kind": "measure"},
                    {"table": "Sales", "field": "Revenue incl Freight",
                     "kind": "measure"},
                ],
            },
            {
                "name": "Product Pricing",
                "path": "pbip_reports/ProductPricing.Report",
                "fields": [
                    {"table": "Product", "field": "ProductName", "kind": "column"},
                    {"table": "Product", "field": "ListPrice", "kind": "column"},
                    {"table": "Product", "field": "Avg List Price", "kind": "measure"},
                    {"table": "Sales", "field": "Avg Unit Price", "kind": "measure"},
                ],
            },
        ]
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "reports.json"
    path.write_text(json.dumps(reports, indent=2), encoding="utf-8")
    return path


def build(db_path: str | Path = DEFAULT_DB) -> None:
    """Run all transforms and emit metadata files."""
    con = duckdb.connect(str(db_path))
    try:
        build_silver(con)
        build_gold(con)
    finally:
        con.close()
    write_semantic_model()
    write_reports()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()
    build(args.db)
    print("Silver + Gold layers built; semantic model and reports metadata written.")
