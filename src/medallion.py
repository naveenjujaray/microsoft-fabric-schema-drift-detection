"""Medallion layer definitions and lineage-graph assembly.

The Bronze->Silver and Silver->Gold column mappings for the
AdventureWorksLT demo are declared here as plain data so both the
transform builder (``sample_data/build_medallion.py``) and the lineage
graph derive from a single source of truth — the mapping IS the
transformation contract we watch for drift.

In live mode the same structures can be loaded from a YAML/JSON
lineage manifest exported from your Dataflow Gen2 / pipeline
definitions.
"""

from __future__ import annotations

from .backends.base import Layer, LayerSchema
from .lineage import LineageGraph
from .lineage_manifest import LineageManifest

# ---------------------------------------------------------------------------
# Bronze -> Silver: (bronze_table, bronze_col) -> (silver_table, silver_col)
BRONZE_TO_SILVER: list[tuple[str, str, str, str]] = [
    # Customer
    ("Customer", "CustomerID", "customers", "customer_id"),
    ("Customer", "FirstName", "customers", "first_name"),
    ("Customer", "LastName", "customers", "last_name"),
    ("Customer", "CompanyName", "customers", "company_name"),
    ("Customer", "EmailAddress", "customers", "email"),
    ("Customer", "Phone", "customers", "phone"),
    # Product
    ("Product", "ProductID", "products", "product_id"),
    ("Product", "Name", "products", "product_name"),
    ("Product", "ProductNumber", "products", "product_number"),
    ("Product", "Color", "products", "color"),
    ("Product", "StandardCost", "products", "standard_cost"),
    ("Product", "ListPrice", "products", "list_price"),
    ("Product", "ProductCategoryID", "products", "category_id"),
    ("ProductCategory", "ProductCategoryID", "product_categories", "category_id"),
    ("ProductCategory", "Name", "product_categories", "category_name"),
    # Sales
    ("SalesOrderHeader", "SalesOrderID", "sales_orders", "order_id"),
    ("SalesOrderHeader", "OrderDate", "sales_orders", "order_date"),
    ("SalesOrderHeader", "DueDate", "sales_orders", "due_date"),
    ("SalesOrderHeader", "ShipDate", "sales_orders", "ship_date"),
    ("SalesOrderHeader", "CustomerID", "sales_orders", "customer_id"),
    ("SalesOrderHeader", "SubTotal", "sales_orders", "subtotal"),
    ("SalesOrderHeader", "TaxAmt", "sales_orders", "tax_amount"),
    ("SalesOrderHeader", "Freight", "sales_orders", "freight"),
    ("SalesOrderHeader", "TotalDue", "sales_orders", "total_due"),
    ("SalesOrderDetail", "SalesOrderID", "sales_order_lines", "order_id"),
    ("SalesOrderDetail", "SalesOrderDetailID", "sales_order_lines", "order_line_id"),
    ("SalesOrderDetail", "ProductID", "sales_order_lines", "product_id"),
    ("SalesOrderDetail", "OrderQty", "sales_order_lines", "quantity"),
    ("SalesOrderDetail", "UnitPrice", "sales_order_lines", "unit_price"),
    ("SalesOrderDetail", "LineTotal", "sales_order_lines", "line_total"),
]

# Silver -> Gold: (silver_table, silver_col) -> (gold_table, gold_col)
SILVER_TO_GOLD: list[tuple[str, str, str, str]] = [
    # Dim_Customer
    ("customers", "customer_id", "Dim_Customer", "CustomerKey"),
    ("customers", "first_name", "Dim_Customer", "FirstName"),
    ("customers", "last_name", "Dim_Customer", "LastName"),
    ("customers", "company_name", "Dim_Customer", "CompanyName"),
    ("customers", "email", "Dim_Customer", "Email"),
    # Dim_Product
    ("products", "product_id", "Dim_Product", "ProductKey"),
    ("products", "product_name", "Dim_Product", "ProductName"),
    ("products", "product_number", "Dim_Product", "ProductNumber"),
    ("products", "color", "Dim_Product", "Color"),
    ("products", "standard_cost", "Dim_Product", "StandardCost"),
    ("products", "list_price", "Dim_Product", "ListPrice"),
    ("product_categories", "category_name", "Dim_Product", "CategoryName"),
    # Dim_Date (derived from order_date)
    ("sales_orders", "order_date", "Dim_Date", "DateKey"),
    # Fact_Sales
    ("sales_order_lines", "order_id", "Fact_Sales", "OrderID"),
    ("sales_order_lines", "order_line_id", "Fact_Sales", "OrderLineID"),
    ("sales_order_lines", "product_id", "Fact_Sales", "ProductKey"),
    ("sales_orders", "customer_id", "Fact_Sales", "CustomerKey"),
    ("sales_orders", "order_date", "Fact_Sales", "DateKey"),
    ("sales_order_lines", "quantity", "Fact_Sales", "Quantity"),
    ("sales_order_lines", "unit_price", "Fact_Sales", "UnitPrice"),
    ("sales_order_lines", "line_total", "Fact_Sales", "LineTotal"),
    ("sales_orders", "freight", "Fact_Sales", "Freight"),
]


def build_lineage_graph(
    semantic_model: LayerSchema | None = None,
    reports: LayerSchema | None = None,
    manifest: LineageManifest | None = None,
) -> LineageGraph:
    """Assemble the full cross-layer lineage graph.

    Bronze->Silver->Gold edges come from the lineage ``manifest`` when
    one is provided (``lineage.manifest`` in config.yaml), otherwise
    from the AdventureWorksLT demo constants above. Gold->SemanticModel
    ->Reports edges are derived from the semantic model definition
    (source tables + DAX refs) and report metadata either way.
    """
    if manifest is not None:
        bronze_to_silver = [m.as_tuple() for m in manifest.bronze_to_silver]
        silver_to_gold = [m.as_tuple() for m in manifest.silver_to_gold]
    else:
        bronze_to_silver = BRONZE_TO_SILVER
        silver_to_gold = SILVER_TO_GOLD

    graph = LineageGraph()
    for src_t, src_c, dst_t, dst_c in bronze_to_silver:
        graph.add_mapping(Layer.BRONZE, src_t, src_c, Layer.SILVER, dst_t, dst_c)
    for src_t, src_c, dst_t, dst_c in silver_to_gold:
        graph.add_mapping(Layer.SILVER, src_t, src_c, Layer.GOLD, dst_t, dst_c)
    if semantic_model is not None:
        graph.register_semantic_model(semantic_model)
    if reports is not None:
        graph.register_reports(reports)
    return graph
