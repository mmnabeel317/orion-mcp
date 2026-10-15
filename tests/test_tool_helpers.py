"""Tests for utils.tool_helpers."""

import asyncio
import os
from types import SimpleNamespace

import pytest

from utils import tool_helpers
from utils.constants import DEFAULT_CONFIG, ORION_CONFIGS_PATH
from utils.tool_helpers import (
    config_path,
    extract_and_set_es_server,
    orion_error_snippet,
    parse_input_vars,
    resolve_config_and_vars,
    split_configs,
)
from utils.utils import current_es_config
from tests.helpers import make_result


def test_parse_input_vars_empty_returns_none():
    """Empty input means "not provided"."""
    assert parse_input_vars("") is None


def test_parse_input_vars_valid_json():
    """Valid JSON is decoded into a dict."""
    assert parse_input_vars('{"platform": "AWS", "workerNodesCount": 24}') == {
        "platform": "AWS",
        "workerNodesCount": 24,
    }


def test_parse_input_vars_malformed_raises():
    """Malformed JSON is reported as ValueError rather than silently ignored."""
    with pytest.raises(ValueError, match="Malformed input_vars JSON"):
        parse_input_vars("{not json")


def test_split_configs_defaults():
    """Empty config_name falls back to the given default, else DEFAULT_CONFIG."""
    assert split_configs(None) == [DEFAULT_CONFIG]
    assert split_configs("") == [DEFAULT_CONFIG]
    assert split_configs(None, default=["a.yaml"]) == ["a.yaml"]


def test_split_configs_strips_and_drops_empty():
    """Comma-separated lists are trimmed and empty entries dropped."""
    assert split_configs(" a.yaml, b.yaml ,,") == ["a.yaml", "b.yaml"]


def test_config_path_joins_configs_dir():
    """Config names resolve under ORION_CONFIGS_PATH."""
    assert config_path("cluster-density.yaml") == os.path.join(ORION_CONFIGS_PATH, "cluster-density.yaml")


def test_orion_error_snippet_prefers_stderr_and_truncates():
    """Snippet prefers stderr, falls back to stdout, and caps at 200 chars."""
    assert orion_error_snippet(make_result(stdout="out", stderr="  err  ")) == "err"
    assert orion_error_snippet(make_result(stdout="out")) == "out"
    assert len(orion_error_snippet(make_result(stderr="x" * 500))) == 200


def test_extract_and_set_es_server_without_ctx_is_noop():
    """No context leaves the ES config unset."""
    extract_and_set_es_server(None)
    assert current_es_config.get() is None


def test_extract_and_set_es_server_sets_context_from_headers(monkeypatch):
    """ES config decrypted from request headers is stored in the context var."""
    seen = {}

    def fake_get_es_config(headers):
        seen.update(headers)
        return {"es_server": "https://es.from.header"}

    monkeypatch.setattr(tool_helpers, "get_es_config_from_headers", fake_get_es_config)
    request = SimpleNamespace(headers={"x-encrypted-context": "blob"})
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=request))

    extract_and_set_es_server(ctx)

    assert seen == {"x-encrypted-context": "blob"}
    assert current_es_config.get() == {"es_server": "https://es.from.header"}


def test_extract_and_set_es_server_rejects_invalid_header(monkeypatch, caplog):
    """A present-but-invalid encrypted header raises instead of falling back to env vars."""

    def broken(_headers):
        raise ValueError("bad key")

    monkeypatch.setattr(tool_helpers, "get_es_config_from_headers", broken)
    request = SimpleNamespace(headers={"x-encrypted-context": "blob"})
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=request))

    with caplog.at_level("WARNING"), pytest.raises(ValueError, match="bad key"):
        extract_and_set_es_server(ctx)

    assert current_es_config.get() is None
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_extract_and_set_es_server_without_header_keeps_env_fallback(monkeypatch):
    """No encrypted header leaves the context unset so env vars are used."""
    monkeypatch.setattr(tool_helpers, "get_es_config_from_headers", lambda _headers: None)
    request = SimpleNamespace(headers={})
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=request))

    extract_and_set_es_server(ctx)

    assert current_es_config.get() is None


def test_resolve_config_and_vars():
    """Config defaults to DEFAULT_CONFIG and input_vars JSON is parsed."""
    assert asyncio.run(resolve_config_and_vars(None, None, "4.20")) == (DEFAULT_CONFIG, None)
    assert asyncio.run(resolve_config_and_vars(None, "x.yaml", "4.20", '{"a": 1}')) == ("x.yaml", {"a": 1})
