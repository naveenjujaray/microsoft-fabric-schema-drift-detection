"""Cross-workspace topology: which Fabric workspace owns which layer.

Enterprise Fabric estates rarely live in one workspace. A typical
topology is::

    Workspace A (Ingestion)      Lakehouse: Bronze + Silver
          | OneLake shortcut
          v
    Workspace B (Enterprise DW)  Warehouse: Gold, Semantic Model
          | cross-workspace report binding
          v
    Workspace C (Reporting Hub)  Power BI reports

A ``WorkspaceRegistry`` is loaded from a JSON manifest that declares
the workspaces (id, name, tenant), the Fabric items they contain
(with the medallion layers each item hosts) and the typed links that
connect them (shortcuts, OneLake shortcuts, mirrored databases,
semantic-model references...). The lineage engine uses the registry to
recognize when a drift's blast radius crosses a workspace boundary and
emits ``cross_workspace_break`` drift records for those targets.

Manifest shape (see ``sample_data/workspaces.json``)::

    {
      "tenant_id": "...",
      "workspaces": [
        {"workspace_id": "...", "name": "Contoso-Ingestion",
         "tenant_id": "...",          # optional; defaults to top-level
         "items": [
           {"item_id": "...", "type": "Lakehouse",
            "name": "IngestionLakehouse", "layers": ["bronze", "silver"]}
         ]}
      ],
      "links": [
        {"type": "onelake_shortcut",
         "from": {"workspace": "Contoso-Ingestion", "layer": "silver"},
         "to":   {"workspace": "Contoso-EDW",       "layer": "gold"}}
      ]
    }
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .backends.base import Layer

logger = logging.getLogger(__name__)

#: link types understood by the lineage engine
LINK_TYPES = frozenset({
    "shortcut",
    "onelake_shortcut",
    "lakehouse",
    "warehouse",
    "semantic_model",
    "mirrored_database",
    "cross_reference",
})


@dataclass(frozen=True)
class WorkspaceItem:
    """One Fabric item (artifact) inside a workspace."""

    item_id: str
    item_type: str  # Lakehouse | Warehouse | SemanticModel | Report | ...
    name: str
    layers: tuple[Layer, ...] = ()


@dataclass(frozen=True)
class WorkspaceInfo:
    """One Fabric workspace plus the items it hosts."""

    workspace_id: str
    name: str
    tenant_id: str = ""
    items: tuple[WorkspaceItem, ...] = ()

    def item_for_layer(self, layer: Layer) -> WorkspaceItem | None:
        for item in self.items:
            if layer in item.layers:
                return item
        return None


@dataclass(frozen=True)
class WorkspaceLink:
    """A typed cross-workspace connection between two layers."""

    link_type: str
    src_workspace: str  # workspace name
    dst_workspace: str
    src_layer: Layer
    dst_layer: Layer


class WorkspaceManifestError(ValueError):
    """The workspace manifest is malformed."""


@dataclass
class WorkspaceRegistry:
    """Resolves layers/nodes to workspaces and knows the links between them."""

    workspaces: list[WorkspaceInfo] = field(default_factory=list)
    links: list[WorkspaceLink] = field(default_factory=list)
    tenant_id: str = ""

    # ------------------------------------------------------------------
    @classmethod
    def from_manifest(cls, manifest: dict[str, Any]) -> "WorkspaceRegistry":
        tenant = str(manifest.get("tenant_id", ""))
        workspaces: list[WorkspaceInfo] = []
        for ws in manifest.get("workspaces", []):
            items = []
            for item in ws.get("items", []):
                try:
                    layers = tuple(Layer(v) for v in item.get("layers", []))
                except ValueError as exc:
                    raise WorkspaceManifestError(
                        f"workspace {ws.get('name')!r}: bad layer in item "
                        f"{item.get('name')!r}: {exc}"
                    ) from exc
                items.append(WorkspaceItem(
                    item_id=str(item.get("item_id", "")),
                    item_type=str(item.get("type", "")),
                    name=str(item.get("name", "")),
                    layers=layers,
                ))
            workspaces.append(WorkspaceInfo(
                workspace_id=str(ws.get("workspace_id", "")),
                name=str(ws.get("name", "")),
                tenant_id=str(ws.get("tenant_id", "") or tenant),
                items=tuple(items),
            ))
        names = [w.name for w in workspaces]
        if len(names) != len(set(names)):
            raise WorkspaceManifestError("duplicate workspace names in manifest")
        by_name = {w.name: w for w in workspaces}

        links: list[WorkspaceLink] = []
        for link in manifest.get("links", []):
            ltype = str(link.get("type", ""))
            if ltype not in LINK_TYPES:
                raise WorkspaceManifestError(
                    f"unknown link type {ltype!r}; expected one of "
                    f"{sorted(LINK_TYPES)}"
                )
            src, dst = link.get("from", {}), link.get("to", {})
            for endpoint in (src, dst):
                if endpoint.get("workspace") not in by_name:
                    raise WorkspaceManifestError(
                        f"link references unknown workspace "
                        f"{endpoint.get('workspace')!r}"
                    )
            try:
                src_layer = Layer(str(src.get("layer", "")))
                dst_layer = Layer(str(dst.get("layer", "")))
            except ValueError as exc:
                raise WorkspaceManifestError(
                    f"link {ltype!r}: bad layer ({exc})"
                ) from exc
            links.append(WorkspaceLink(
                link_type=ltype,
                src_workspace=str(src["workspace"]),
                dst_workspace=str(dst["workspace"]),
                src_layer=src_layer,
                dst_layer=dst_layer,
            ))
        return cls(workspaces=workspaces, links=links, tenant_id=tenant)

    @classmethod
    def load(cls, path: str | Path) -> "WorkspaceRegistry":
        path = Path(path)
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceManifestError(
                f"cannot read workspace manifest {path}: {exc}"
            ) from exc
        return cls.from_manifest(manifest)

    # ------------------------------------------------------------------
    def workspace_for_layer(self, layer: Layer) -> WorkspaceInfo | None:
        for ws in self.workspaces:
            if ws.item_for_layer(layer) is not None:
                return ws
        return None

    def workspace_for_node(self, node: str) -> WorkspaceInfo | None:
        """Workspace owning a lineage node id (``layer:...``)."""
        layer_name = node.split(":", 1)[0]
        try:
            layer = Layer(layer_name)
        except ValueError:
            return None
        return self.workspace_for_layer(layer)

    def link_between(
        self, src_layer: Layer, dst_layer: Layer
    ) -> WorkspaceLink | None:
        for link in self.links:
            if link.src_layer is src_layer and link.dst_layer is dst_layer:
                return link
        return None

    def crosses_tenant(self, a: WorkspaceInfo, b: WorkspaceInfo) -> bool:
        return bool(a.tenant_id and b.tenant_id and a.tenant_id != b.tenant_id)

    # ------------------------------------------------------------------
    def workspace_path(self, node: str) -> str:
        """Human-readable location: ``Workspace / Item(Type) / node``."""
        ws = self.workspace_for_node(node)
        if ws is None:
            return node
        layer = Layer(node.split(":", 1)[0])
        item = ws.item_for_layer(layer)
        rest = node.split(":", 1)[1]
        if item is None:
            return f"{ws.name} / {rest}"
        return f"{ws.name} / {item.name} ({item.item_type}) / {rest}"

    def blast_radius(self, nodes: list[str]) -> dict[str, int]:
        """Impacted node count per workspace name (unknown -> '(unmapped)')."""
        counts: dict[str, int] = {}
        for node in nodes:
            ws = self.workspace_for_node(node)
            key = ws.name if ws else "(unmapped)"
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))


def load_registry(path: str | Path | None) -> WorkspaceRegistry | None:
    """Load a registry if a manifest path is configured and exists."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        logger.debug("workspace manifest not found at %s; single-workspace mode", p)
        return None
    registry = WorkspaceRegistry.load(p)
    logger.info(
        "workspace manifest loaded: %d workspace(s), %d link(s)",
        len(registry.workspaces), len(registry.links),
    )
    return registry
