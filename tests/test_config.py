"""Config parsing: watch scope (layers + mode)."""

from __future__ import annotations

import pytest

from src.backends.base import Layer
from src.config import WatchConfig, parse_watch_config


def test_no_watch_block_means_full_watch_all_layers():
    watch = parse_watch_config({})
    assert watch.mode == "full"
    assert watch.layers == []          # empty = every layer
    assert all(watch.includes(layer) for layer in Layer)


def test_layer_allowlist_parsed_and_filters():
    watch = parse_watch_config(
        {"watch": {"layers": ["bronze", "silver"]}}
    )
    assert watch.layers == [Layer.BRONZE, Layer.SILVER]
    assert watch.includes(Layer.BRONZE)
    assert not watch.includes(Layer.GOLD)
    assert not watch.includes(Layer.REPORTS)


def test_boundaries_mode_parsed():
    watch = parse_watch_config({"watch": {"mode": "boundaries"}})
    assert watch.mode == "boundaries"


def test_bad_layer_name_rejected_with_name():
    with pytest.raises(ValueError, match="platinum"):
        parse_watch_config({"watch": {"layers": ["platinum"]}})


def test_bad_mode_rejected():
    with pytest.raises(ValueError, match="watch.mode"):
        parse_watch_config({"watch": {"mode": "partial"}})


def test_watch_config_is_plain_dataclass():
    watch = WatchConfig(layers=[Layer.SILVER], mode="boundaries")
    assert watch.includes(Layer.SILVER) and not watch.includes(Layer.GOLD)
