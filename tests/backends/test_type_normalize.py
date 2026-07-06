"""Type normalization: source dialects -> one canonical type vocabulary."""

from __future__ import annotations

import logging

import pytest

from fabric_drift_detective.backends.type_normalize import (
    ANSI_TYPE_MAP,
    CANONICAL_TYPES,
    TypeNormalizer,
)


@pytest.fixture
def normalizer() -> TypeNormalizer:
    return TypeNormalizer(ANSI_TYPE_MAP, source="ansi")


def test_canonical_set_is_the_documented_vocabulary():
    assert CANONICAL_TYPES == frozenset({
        "string", "int", "bigint", "decimal", "float",
        "bool", "timestamp", "date", "binary",
    })


@pytest.mark.parametrize(("raw", "expected"), [
    ("VARCHAR", "string"),
    ("varchar", "string"),          # case-insensitive
    ("NVARCHAR", "string"),
    ("TEXT", "string"),
    ("INTEGER", "int"),
    ("SMALLINT", "int"),
    ("BIGINT", "bigint"),
    ("DECIMAL", "decimal"),
    ("NUMERIC", "decimal"),
    ("DOUBLE PRECISION", "float"),
    ("BOOLEAN", "bool"),
    ("TIMESTAMP", "timestamp"),
    ("DATETIME2", "timestamp"),
    ("DATE", "date"),
    ("VARBINARY", "binary"),
])
def test_ansi_map_covers_common_dialect_spellings(normalizer, raw, expected):
    assert normalizer.normalize(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), [
    ("NVARCHAR(50)", "string(50)"),
    ("DECIMAL(19,4)", "decimal(19,4)"),
    ("VARCHAR(MAX)", "string(MAX)"),
])
def test_type_parameters_are_preserved(normalizer, raw, expected):
    """Precision/scale/length feed precision_scale_change detection -
    normalization must never strip them."""
    assert normalizer.normalize(raw) == expected


def test_unmapped_type_passes_through_with_warning(normalizer, caplog):
    with caplog.at_level(logging.WARNING):
        assert normalizer.normalize("GEOGRAPHY") == "GEOGRAPHY"
    assert "GEOGRAPHY" in caplog.text and "ansi" in caplog.text


def test_unmapped_type_warns_once_per_type(normalizer, caplog):
    with caplog.at_level(logging.WARNING):
        normalizer.normalize("GEOGRAPHY")
        normalizer.normalize("GEOGRAPHY")
    assert caplog.text.count("GEOGRAPHY") == 1


def test_custom_map_overrides_merge_over_ansi():
    custom = TypeNormalizer({**ANSI_TYPE_MAP, "NUMBER": "decimal"}, source="x")
    assert custom.normalize("NUMBER(38,0)") == "decimal(38,0)"


def test_map_values_must_be_canonical():
    with pytest.raises(ValueError, match="varchar2"):
        TypeNormalizer({"FOO": "varchar2"}, source="bad")


def test_cross_source_equivalence():
    """The whole point: same logical type from two dialects -> equal strings."""
    hana_like = TypeNormalizer({**ANSI_TYPE_MAP, "NVARCHAR": "string"}, source="a")
    snow_like = TypeNormalizer({**ANSI_TYPE_MAP, "STRING": "string"}, source="b")
    assert hana_like.normalize("NVARCHAR(100)") == snow_like.normalize("STRING(100)")
