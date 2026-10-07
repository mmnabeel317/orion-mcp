"""Unit tests for orion_mcp._split_configs."""

import asyncio
import importlib
import os
import sys
from unittest import mock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _import_orion_mcp():
    """Import orion_mcp without network access.

    The module lists Orion configs at import time (GitHub API, then a local
    directory), so stub that discovery out before importing.
    """
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    with mock.patch("utils.utils.list_orion_configs", return_value=[]):
        return importlib.import_module("orion_mcp")


orion_mcp = _import_orion_mcp()
DEFAULT_CONFIG = orion_mcp.DEFAULT_CONFIG
split_configs = orion_mcp._split_configs  # pylint: disable=protected-access

EMPTY_INPUTS = [None, "", "   ", " , , ", ",,,", " ,\t, "]


@pytest.mark.parametrize("config_name", EMPTY_INPUTS)
def test_empty_input_without_default_uses_default_config(config_name):
    """Empty normalized input with no default falls back to DEFAULT_CONFIG."""
    assert split_configs(config_name) == [DEFAULT_CONFIG]


@pytest.mark.parametrize("config_name", EMPTY_INPUTS)
def test_empty_input_uses_supplied_default(config_name):
    """Empty normalized input returns the supplied default."""
    default = ["node-density.yaml", "udn-l3.yaml"]
    assert split_configs(config_name, default=default) == default


@pytest.mark.parametrize("config_name", EMPTY_INPUTS)
def test_empty_input_with_empty_default_stays_empty(config_name):
    """An explicitly supplied empty default is honored."""
    assert split_configs(config_name, default=[]) == []


@pytest.mark.parametrize(
    ("config_name", "expected"),
    [
        ("a.yaml", ["a.yaml"]),
        ("a.yaml, b.yaml", ["a.yaml", "b.yaml"]),
        ("  a.yaml  ,b.yaml ", ["a.yaml", "b.yaml"]),
        ("a.yaml,,a.yaml , b", ["a.yaml", "a.yaml", "b"]),
        (",b.yaml,a.yaml,", ["b.yaml", "a.yaml"]),
    ],
)
def test_names_are_trimmed_with_order_and_duplicates_preserved(config_name, expected):
    """Names are trimmed, empty entries dropped, order and duplicates kept."""
    assert split_configs(config_name) == expected
    assert split_configs(config_name, default=["x.yaml"]) == expected


BLANK_CONFIG_NAMES = [None, "", "   ", " , , ", ",,,"]


@pytest.mark.parametrize("config_name", BLANK_CONFIG_NAMES)
def test_report_on_pr_blank_config_name_is_rejected(config_name):
    """openshift_report_on_pr rejects every blank config_name without running Orion."""
    with mock.patch.object(orion_mcp, "_extract_and_set_es_server"), mock.patch.object(
        orion_mcp, "run_orion", new=mock.AsyncMock()
    ) as run_orion:
        result = asyncio.run(
            orion_mcp.openshift_report_on_pr(config_name=config_name, input_vars="", ctx=None)
        )
    assert result["summaries"] == []
    assert "config_name is required" in result["error"]
    run_orion.assert_not_called()
