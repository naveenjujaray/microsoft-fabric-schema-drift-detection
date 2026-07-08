"""Azure Cosmos DB backend: document sampling -> inferred schema, contract."""

from __future__ import annotations

import pytest

from fabric_drift_detective.backends.base import Layer
from fabric_drift_detective.backends.cosmos_backend import CosmosBackend
from tests.backends.backend_contract import SchemaBackendContract

DOCS = [
    {
        "id": "1", "name": "widget", "qty": 2, "price": 1.5,
        "active": True, "meta": {"a": 1}, "tags": ["x"],
        "_rid": "sys", "_ts": 1700000000,
    },
    {"id": "2", "name": None, "qty": 5, "price": 2.0, "active": False,
     "meta": {}, "tags": [], "extra": "only-here"},
]


class FakeContainer:
    def __init__(self, docs):
        self._docs = docs

    def query_items(self, query, parameters=None,
                    enable_cross_partition_query=False, **kwargs):
        return iter(self._docs)


class FakeDatabase:
    def __init__(self, containers):
        self._containers = containers

    def list_containers(self):
        return iter({"id": name} for name in self._containers)

    def get_container_client(self, name):
        return FakeContainer(self._containers[name])


class FakeCosmosClient:
    def __init__(self, containers):
        self._db = FakeDatabase(containers)

    def get_database_client(self, name):
        return self._db


def _backend(containers=None, **cfg):
    if containers is None:
        containers = {"items": DOCS}
    config = {"database": "shop", "layer": "bronze", **cfg}
    return CosmosBackend(
        config, client_factory=lambda: FakeCosmosClient(containers)
    )


def test_documents_infer_typed_columns():
    cols = _backend().get_schema(Layer.BRONZE).tables["items"].columns
    assert cols["id"].dtype == "string"
    assert cols["qty"].dtype == "int"
    assert cols["price"].dtype == "float"
    assert cols["active"].dtype == "bool"  # bool, not int
    assert cols["meta"].dtype == "object"
    assert cols["tags"].dtype == "array"


def test_nullability_from_nulls_and_missing_fields():
    cols = _backend().get_schema(Layer.BRONZE).tables["items"].columns
    assert cols["name"].nullable is True    # explicit null in doc 2
    assert cols["extra"].nullable is True   # missing from doc 1
    assert cols["qty"].nullable is False    # present + non-null everywhere


def test_system_fields_stripped():
    cols = _backend().get_schema(Layer.BRONZE).tables["items"].columns
    assert "_rid" not in cols and "_ts" not in cols


def test_conflicting_types_become_mixed():
    docs = [{"id": "1", "v": 1}, {"id": "2", "v": "one"}]
    cols = (
        _backend(containers={"c": docs})
        .get_schema(Layer.BRONZE).tables["c"].columns
    )
    assert cols["v"].dtype == "mixed"


def test_int_and_float_fold_to_float():
    """JSON serializes 2.0 as 2; a whole-number sample must not flip a
    numeric field to 'mixed'."""
    docs = [{"id": "1", "price": 1.5}, {"id": "2", "price": 2}]
    cols = (
        _backend(containers={"c": docs})
        .get_schema(Layer.BRONZE).tables["c"].columns
    )
    assert cols["price"].dtype == "float"


def test_all_null_field_is_skipped_not_guessed():
    """A field never seen with a value has an unknowable type; guessing
    'string' would fire a false CRITICAL type_change when values arrive.
    Skipping means its later appearance is a benign column_add."""
    docs = [{"id": "1", "discount": None}, {"id": "2", "discount": None}]
    cols = (
        _backend(containers={"c": docs})
        .get_schema(Layer.BRONZE).tables["c"].columns
    )
    assert "discount" not in cols
    assert "id" in cols


def test_user_underscore_fields_kept_only_system_stripped():
    docs = [{"id": "1", "_sourceSystem": "sap", "_rid": "x", "_etag": "y"}]
    cols = (
        _backend(containers={"c": docs})
        .get_schema(Layer.BRONZE).tables["c"].columns
    )
    assert "_sourceSystem" in cols
    assert "_rid" not in cols and "_etag" not in cols


def test_empty_container_logs_warning(caplog):
    import logging

    backend = _backend(containers={"empty_one": []})
    with caplog.at_level(logging.WARNING):
        schema = backend.get_schema(Layer.BRONZE)
    assert schema.tables["empty_one"].columns == {}  # contract: no crash
    assert any("empty_one" in r.message for r in caplog.records)


def test_ordinals_are_alphabetical_and_deterministic():
    cols = _backend().get_schema(Layer.BRONZE).tables["items"].columns
    ordered = sorted(cols, key=lambda n: cols[n].ordinal)
    assert ordered == sorted(cols)


def test_explicit_container_list_limits_scan():
    data = {"a": [{"id": "1"}], "b": [{"id": "2"}]}
    backend = CosmosBackend(
        {"database": "shop", "containers": ["a"]},
        client_factory=lambda: FakeCosmosClient(data),
    )
    assert set(backend.get_schema(Layer.BRONZE).tables) == {"a"}


def test_missing_database_config_rejected():
    with pytest.raises(ValueError, match="database"):
        CosmosBackend({}, client_factory=lambda: FakeCosmosClient({}))


def test_missing_env_names_variables(monkeypatch):
    for var in ("COSMOS_ENDPOINT", "COSMOS_KEY"):
        monkeypatch.delenv(var, raising=False)
    backend = CosmosBackend({"database": "shop"})
    with pytest.raises(OSError, match="COSMOS_ENDPOINT"):
        backend.get_schema(Layer.BRONZE)


class TestCosmosBackendContract(SchemaBackendContract):
    @pytest.fixture
    def backend(self):
        return _backend()

    @pytest.fixture
    def empty_backend(self):
        return _backend(containers={})


def test_registry_builds_cosmos_backend():
    from fabric_drift_detective.backends import make_source_backend

    backend = make_source_backend(
        {"type": "cosmos", "database": "shop"},
        connection_factory=lambda: FakeCosmosClient({"items": DOCS}),
    )
    assert isinstance(backend, CosmosBackend)
