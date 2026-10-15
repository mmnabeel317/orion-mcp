"""Tests for pure helpers in utils.utils."""

import asyncio
import math
from datetime import datetime, timezone

import pytest

from utils.utils import (
    compute_correlation,
    current_es_config,
    filter_data_by_timestamp,
    get_data_source,
    get_es_benchmark_index,
    get_es_metadata_index,
    orion_configs,
    parse_nightly_version,
    parse_timestamp,
    redact_url,
    resolve_env_var,
    summarize_result,
)
from tests.helpers import make_result


def test_parse_nightly_version_nightly():
    """Nightly strings yield the major version and build timestamp."""
    info = parse_nightly_version("4.22.0-0.nightly-2026-01-05-203335")
    assert info.is_nightly
    assert info.major_version == "4.22"
    assert info.nightly_date == datetime(2026, 1, 5, 20, 33, 35)


def test_parse_nightly_version_ga():
    """GA versions are recognised but are not nightlies."""
    info = parse_nightly_version(" 4.17.1 ")
    assert not info.is_nightly
    assert info.major_version == "4.17"
    assert info.nightly_date is None


def test_parse_nightly_version_invalid():
    """Unrecognised formats raise ValueError."""
    with pytest.raises(ValueError, match="Invalid version format"):
        parse_nightly_version("latest")


def test_parse_timestamp_formats():
    """Epoch numbers, numeric strings and ISO strings are all parsed as UTC."""
    expected = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
    epoch = int(expected.timestamp())
    assert parse_timestamp(epoch) == expected
    assert parse_timestamp(str(epoch)) == expected
    assert parse_timestamp("2025-01-15T12:00:00.123Z") == expected
    assert parse_timestamp("garbage") is None
    assert parse_timestamp(None) is None


def test_filter_data_by_timestamp():
    """Only entries on or before the cutoff are kept; unparseable timestamps are dropped."""
    cutoff = datetime(2025, 1, 15, tzinfo=timezone.utc)
    data = [
        {"timestamp": "2025-01-14T00:00:00"},
        {"timestamp": "2025-01-15T00:00:00"},
        {"timestamp": "2025-01-16T00:00:00"},
        {"timestamp": "bad"},
        {},
    ]
    assert filter_data_by_timestamp(data, cutoff) == data[:2]


def test_compute_correlation():
    """Pearson r for perfectly (anti-)correlated data; NaN for degenerate input."""
    assert compute_correlation([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert compute_correlation([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert math.isnan(compute_correlation([1, 2], [1, 2, 3]))
    assert math.isnan(compute_correlation([1], [1]))
    assert math.isnan(compute_correlation([1, 1, 1], [1, 2, 3]))


def test_orion_configs_returns_basenames():
    """Full config paths are reduced to filenames."""
    assert orion_configs(["/orion/examples/a.yaml", "b.yaml"]) == ["a.yaml", "b.yaml"]


def test_resolve_env_var_precedence(monkeypatch):
    """Primary name wins, then secondary, then default; blank values are ignored."""
    monkeypatch.delenv("orion_test_var", raising=False)
    monkeypatch.delenv("ORION_TEST_VAR", raising=False)
    assert resolve_env_var("orion_test_var", "ORION_TEST_VAR", "default") == "default"
    monkeypatch.setenv("ORION_TEST_VAR", "upper")
    assert resolve_env_var("orion_test_var", "ORION_TEST_VAR", "default") == "upper"
    monkeypatch.setenv("orion_test_var", "  ")
    assert resolve_env_var("orion_test_var", "ORION_TEST_VAR", "default") == "upper"
    monkeypatch.setenv("orion_test_var", "lower")
    assert resolve_env_var("orion_test_var", "ORION_TEST_VAR", "default") == "lower"


def test_get_data_source_prefers_context_over_env(monkeypatch):
    """ES server from the request context overrides ES_SERVER; missing both raises."""
    assert get_data_source() == "http://es.example.com:9200"
    current_es_config.set({"es_server": "https://from-header"})
    assert get_data_source() == "https://from-header"
    current_es_config.set(None)
    monkeypatch.delenv("ES_SERVER")
    with pytest.raises(EnvironmentError):
        get_data_source()


def test_redact_url_strips_credentials():
    """Userinfo and sensitive query values are removed; plain URLs are unchanged."""
    assert redact_url("http://es.example.com:9200") == "http://es.example.com:9200"
    assert redact_url("https://user:pw@es.example.com:9200/idx") == "https://es.example.com:9200/idx"
    assert redact_url("https://es.example.com?api_key=abc&size=5") == (
        "https://es.example.com?api_key=REDACTED&size=5"
    )


def test_es_indices_prefer_context(monkeypatch):
    """Index names come from the request context first, then env/defaults."""
    for name in ("es_metadata_index", "ES_METADATA_INDEX", "es_benchmark_index", "ES_BENCHMARK_INDEX"):
        monkeypatch.delenv(name, raising=False)
    assert get_es_metadata_index() == "perf_scale_ci*"
    assert get_es_benchmark_index() == "ripsaw-kube-burner-*"
    current_es_config.set({"es_server": "x", "es_metadata_index": "meta*", "es_benchmark_index": "bench*"})
    assert get_es_metadata_index() == "meta*"
    assert get_es_benchmark_index() == "bench*"


def test_summarize_result_groups_metric_values():
    """Values are collected per metric, optionally isolated to a single metric."""
    runs = [
        {"timestamp": 1, "metrics": {"a": {"value": 1.0}, "b": {"value": 10.0}}},
        {"timestamp": 2, "metrics": {"a": {"value": 2.0}, "b": {"value": 20.0}}},
    ]
    summary = asyncio.run(summarize_result(make_result(runs)))
    assert summary["a"] == {"value": [1.0, 2.0]}
    assert summary["b"] == {"value": [10.0, 20.0]}
    assert summary["timestamp"] == 2
    assert summary["runs"] == runs

    isolated = asyncio.run(summarize_result(make_result(runs), isolate="b"))
    assert "a" not in isolated
    assert isolated["b"] == {"value": [10.0, 20.0]}


def test_summarize_result_no_data_and_errors():
    """Exit code 3 and empty output mean no data; bad output yields an error string."""
    assert asyncio.run(summarize_result(make_result("", returncode=3))) == {}
    assert asyncio.run(summarize_result(make_result([]))) == {}
    assert asyncio.run(summarize_result(make_result("not json"))).startswith("Error")
