"""Reusable contract suite every SchemaBackend implementation must pass.

Usage — subclass in your backend's test module and provide a backend:

    from tests.backends.backend_contract import SchemaBackendContract

    class TestMyBackendContract(SchemaBackendContract):
        @pytest.fixture
        def backend(self):
            return MyBackend(...)          # wired to a fake/mock source

        @pytest.fixture
        def empty_backend(self):
            return MyBackend(...)          # wired to an EMPTY source

Run ``pytest tests/backends/`` — every test in this class executes
against your fixtures. All green = your backend honors the contract the
drift engine relies on; the engine itself never needs to know your
backend exists.

The contract:

1. ``list_layers()`` returns a non-empty list of ``Layer`` values.
2. ``get_schema(layer)`` returns a ``LayerSchema`` whose ``layer``
   matches the request, for every advertised layer.
3. ``get_all_schemas()`` returns exactly one ``LayerSchema`` per
   advertised layer.
4. Every column is a ``ColumnSchema`` with a non-empty ``name``, a
   non-empty ``dtype`` string, and an integer ``ordinal`` —
   the drift engine sorts and compares on these.
5. An EMPTY source yields an empty ``LayerSchema`` (no tables), never
   an exception — first-run against a fresh workspace must not crash.
"""

from __future__ import annotations

import pytest

from fabric_drift_detective.backends.base import (
    ColumnSchema,
    Layer,
    LayerSchema,
    SchemaBackend,
)


class SchemaBackendContract:
    """Inherit and provide ``backend`` / ``empty_backend`` fixtures."""

    @pytest.fixture
    def backend(self) -> SchemaBackend:
        raise NotImplementedError(
            "provide a `backend` fixture wired to a fake source with data"
        )

    @pytest.fixture
    def empty_backend(self) -> SchemaBackend:
        raise NotImplementedError(
            "provide an `empty_backend` fixture wired to an empty source"
        )

    # -- 1. layers ------------------------------------------------------
    def test_list_layers_returns_layer_enum_values(self, backend):
        layers = backend.list_layers()
        assert layers, "backend must advertise at least one layer"
        assert all(isinstance(layer, Layer) for layer in layers)

    # -- 2. get_schema --------------------------------------------------
    def test_get_schema_returns_matching_layer_schema(self, backend):
        for layer in backend.list_layers():
            schema = backend.get_schema(layer)
            assert isinstance(schema, LayerSchema)
            assert schema.layer is layer

    # -- 3. get_all_schemas ---------------------------------------------
    def test_get_all_schemas_covers_every_advertised_layer(self, backend):
        schemas = backend.get_all_schemas()
        assert set(schemas.keys()) == set(backend.list_layers())
        for layer, schema in schemas.items():
            assert isinstance(schema, LayerSchema)
            assert schema.layer is layer

    # -- 4. column integrity --------------------------------------------
    def test_columns_have_name_dtype_ordinal(self, backend):
        saw_a_column = False
        for schema in backend.get_all_schemas().values():
            for table in schema.tables.values():
                assert table.name, "table must have a name"
                for key, col in table.columns.items():
                    saw_a_column = True
                    assert isinstance(col, ColumnSchema)
                    assert col.name and isinstance(col.name, str)
                    assert key == col.name, "columns dict keyed by column name"
                    assert col.dtype and isinstance(col.dtype, str)
                    assert isinstance(col.ordinal, int)
        assert saw_a_column, "backend fixture must expose at least one column"

    # -- 5. empty source ------------------------------------------------
    def test_empty_source_yields_empty_layer_schema_not_crash(
        self, empty_backend
    ):
        for layer in empty_backend.list_layers():
            schema = empty_backend.get_schema(layer)
            assert isinstance(schema, LayerSchema)
            assert schema.tables == {}
