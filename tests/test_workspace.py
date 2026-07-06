"""WorkspaceRegistry: manifest parsing, resolution, blast radius."""

from __future__ import annotations

import json

import pytest

from fabric_drift_detective.backends.base import Layer
from fabric_drift_detective.workspace import (
    WorkspaceManifestError,
    WorkspaceRegistry,
    load_registry,
)

MANIFEST = {
    "tenant_id": "tenant-1",
    "workspaces": [
        {
            "workspace_id": "ws-a", "name": "Ingestion",
            "items": [{"item_id": "lh-1", "type": "Lakehouse",
                       "name": "RawLake", "layers": ["bronze", "silver"]}],
        },
        {
            "workspace_id": "ws-b", "name": "EDW",
            "items": [
                {"item_id": "wh-1", "type": "Warehouse",
                 "name": "DW", "layers": ["gold"]},
                {"item_id": "sm-1", "type": "SemanticModel",
                 "name": "SalesModel", "layers": ["semantic_model"]},
            ],
        },
        {
            "workspace_id": "ws-c", "name": "Reporting",
            "tenant_id": "tenant-2",
            "items": [{"item_id": "rp-1", "type": "Report",
                       "name": "SalesReports", "layers": ["reports"]}],
        },
    ],
    "links": [
        {"type": "onelake_shortcut",
         "from": {"workspace": "Ingestion", "layer": "silver"},
         "to": {"workspace": "EDW", "layer": "gold"}},
        {"type": "semantic_model",
         "from": {"workspace": "EDW", "layer": "semantic_model"},
         "to": {"workspace": "Reporting", "layer": "reports"}},
    ],
}


@pytest.fixture
def registry() -> WorkspaceRegistry:
    return WorkspaceRegistry.from_manifest(MANIFEST)


def test_workspace_for_layer(registry):
    assert registry.workspace_for_layer(Layer.SILVER).name == "Ingestion"
    assert registry.workspace_for_layer(Layer.GOLD).name == "EDW"
    assert registry.workspace_for_layer(Layer.REPORTS).name == "Reporting"


def test_workspace_for_node(registry):
    assert registry.workspace_for_node("silver:customers.email").name == "Ingestion"
    assert registry.workspace_for_node("nonsense-node") is None


def test_link_between(registry):
    link = registry.link_between(Layer.SILVER, Layer.GOLD)
    assert link is not None and link.link_type == "onelake_shortcut"
    assert registry.link_between(Layer.BRONZE, Layer.REPORTS) is None


def test_tenant_boundary(registry):
    edw = registry.workspace_for_layer(Layer.GOLD)
    reporting = registry.workspace_for_layer(Layer.REPORTS)
    ingestion = registry.workspace_for_layer(Layer.SILVER)
    assert registry.crosses_tenant(edw, reporting)  # tenant-1 vs tenant-2
    assert not registry.crosses_tenant(edw, ingestion)


def test_workspace_path(registry):
    path = registry.workspace_path("gold:Dim_Customer.Email")
    assert path == "EDW / DW (Warehouse) / Dim_Customer.Email"
    assert registry.workspace_path("unknown:x") == "unknown:x"


def test_blast_radius_groups_by_workspace(registry):
    nodes = [
        "gold:Dim_Customer.Email",
        "semantic_model:Customer.Email",
        "reports:Customer Detail.Customer.Email",
        "reports:Exec Summary.Customer.Email",
    ]
    assert registry.blast_radius(nodes) == {"EDW": 2, "Reporting": 2}


def test_unknown_link_type_rejected():
    bad = json.loads(json.dumps(MANIFEST))
    bad["links"][0]["type"] = "teleporter"
    with pytest.raises(WorkspaceManifestError, match="teleporter"):
        WorkspaceRegistry.from_manifest(bad)


def test_link_to_unknown_workspace_rejected():
    bad = json.loads(json.dumps(MANIFEST))
    bad["links"][0]["to"]["workspace"] = "Nowhere"
    with pytest.raises(WorkspaceManifestError, match="Nowhere"):
        WorkspaceRegistry.from_manifest(bad)


def test_duplicate_workspace_names_rejected():
    bad = json.loads(json.dumps(MANIFEST))
    bad["workspaces"][1]["name"] = "Ingestion"
    with pytest.raises(WorkspaceManifestError, match="duplicate"):
        WorkspaceRegistry.from_manifest(bad)


def test_load_registry_missing_path_returns_none(tmp_path):
    assert load_registry("") is None
    assert load_registry(tmp_path / "nope.json") is None


def test_load_registry_from_file(tmp_path):
    p = tmp_path / "ws.json"
    p.write_text(json.dumps(MANIFEST), encoding="utf-8")
    reg = load_registry(p)
    assert reg is not None and len(reg.workspaces) == 3


def test_shipped_sample_manifest_is_valid():
    reg = load_registry("sample_data/workspaces.json")
    assert reg is not None
    assert {w.name for w in reg.workspaces} == {
        "Contoso-Ingestion", "Contoso-Enterprise-DW", "Contoso-Reporting",
    }
    assert reg.workspace_for_layer(Layer.REPORTS).name == "Contoso-Reporting"
