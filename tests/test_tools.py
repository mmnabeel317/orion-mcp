"""Tests for the remaining tool modules under components/tools."""

import asyncio
import json
import os

import pytest

from components.tools import configs, correlation, performance_data, pr_analysis, report
from components.tools.configs import get_orion_configs
from components.tools.correlation import metrics_correlation
from components.tools.performance_data import get_orion_performance_data
from components.tools.pr_analysis import _add_percentage_changes, get_pr_details, openshift_report_on_pr
from components.tools.release_date import get_release_date
from components.tools.report import openshift_report_on
from tests.helpers import make_result
from utils.constants import MISSING_CORRELATION_METRICS_MSG, NO_PR_DATA_MSG, ORION_CONFIGS_PATH, PR_CONFIG_REQUIRED_MSG

RUNS = [
    {"timestamp": 1, "metrics": {"podReadyLatency_P99": {"value": 1.0}, "ovnCPU_avg": {"value": 2.0}}},
    {"timestamp": 2, "metrics": {"podReadyLatency_P99": {"value": None}, "ovnCPU_avg": {"value": 4.0}}},
    {"timestamp": 3, "metrics": {"podReadyLatency_P99": {"value": 3.0}, "ovnCPU_avg": {"value": 6.0}}},
]


def test_get_release_date():
    """Known versions return their GA date; unknown versions an error string."""
    assert asyncio.run(get_release_date("4.19")) == "2025-06-17"
    assert asyncio.run(get_release_date("3.11")) == "Invalid version: 3.11"


def test_get_orion_configs_lists_basenames_once(monkeypatch):
    """Configs are fetched once (cached) and returned as filenames."""
    calls = []

    def fake_list():
        calls.append(1)
        return ["cluster-density.yaml", "node-density.yaml"]

    configs.available_configs.cache_clear()
    monkeypatch.setattr(configs, "list_orion_configs", fake_list)
    try:
        assert get_orion_configs() == ["cluster-density.yaml", "node-density.yaml"]
        assert get_orion_configs() == ["cluster-density.yaml", "node-density.yaml"]
        assert len(calls) == 1
    finally:
        configs.available_configs.cache_clear()


def test_get_orion_performance_data_single_config(fake_orion):
    """Single config returns flat values with None filtered out."""
    fake = fake_orion(performance_data, make_result(RUNS))

    out = asyncio.run(get_orion_performance_data("cluster-density.yaml", version="4.22", input_vars='{"a": 1}'))

    assert out == {
        "config": "cluster-density.yaml",
        "metric": "podReadyLatency_P99",
        "version": "4.22",
        "lookback": "15",
        "values": [1.0, 3.0],
        "count": 2,
    }
    assert fake.calls[0]["input_vars"] == {"a": 1}


def test_get_orion_performance_data_multiple_configs(fake_orion):
    """Multiple configs return a results list, with per-config errors for missing data."""
    fake_orion(performance_data, {
        os.path.join(ORION_CONFIGS_PATH, "a.yaml"): make_result(RUNS),
        os.path.join(ORION_CONFIGS_PATH, "b.yaml"): make_result("", returncode=3),
    })

    out = asyncio.run(get_orion_performance_data("a.yaml,b.yaml", metric="ovnCPU_avg"))

    assert out["results"][0]["values"] == [2.0, 4.0, 6.0]
    assert out["results"][1] == {"config": "b.yaml", "error": "No data found for metric ovnCPU_avg"}


def test_openshift_report_on_json(fake_orion):
    """options='json' returns the summarized data per version as JSON text."""
    fake = fake_orion(report, make_result(RUNS))

    out = asyncio.run(openshift_report_on("4.21, 4.22", options="json:ocpVirtVersion", config_name="a.yaml"))

    payload = json.loads(out.text)
    assert payload["config"] == "a.yaml"
    assert payload["display"] == "ocpVirtVersion"
    assert set(payload["data"]) == {"4.21", "4.22"}
    assert [c["version"] for c in fake.calls] == ["4.21", "4.22"]
    assert fake.calls[0]["display"] == "ocpVirtVersion"


def test_openshift_report_on_image(fake_orion):
    """The default output is a PNG image."""
    fake_orion(report, make_result(RUNS))

    out = asyncio.run(openshift_report_on("4.22"))

    assert out.type == "image"
    assert out.mimeType == "image/png"


def test_openshift_report_on_no_data(fake_orion):
    """When no version has data, the collected errors are returned as text."""
    fake_orion(report, make_result("", returncode=3))

    out = asyncio.run(openshift_report_on("4.22", config_name="a.yaml"))

    assert out.type == "text"
    assert out.text == "[a.yaml] No data for version 4.22, metric podReadyLatency_P99"


def test_metrics_correlation(fake_orion):
    """Correlation returns a scatter-plot image, or text when a metric is missing."""
    fake_orion(correlation, make_result([r for r in RUNS if r["metrics"]["podReadyLatency_P99"]["value"]]))

    assert asyncio.run(metrics_correlation()).type == "image"

    out = asyncio.run(metrics_correlation(metric2="doesNotExist"))
    assert out.text == MISSING_CORRELATION_METRICS_MSG


def test_add_percentage_changes():
    """Percent change is computed against the periodic average, None when not computable."""
    pulls = [{"data": [{"metrics": {
        "a": {"value": 15.0},
        "b": {"value": 5.0},
        "c": {"value": 1.0},
        "d": {"value": None},
    }}]}]
    _add_percentage_changes(pulls, {"a": {"value": 10.0}, "b": 10.0, "d": 1.0})

    metrics = pulls[0]["data"][0]["metrics"]
    assert metrics["a"]["percentage_change"] == pytest.approx(50.0)
    assert metrics["b"]["percentage_change"] == pytest.approx(-50.0)
    assert metrics["c"]["percentage_change"] is None
    assert metrics["d"]["percentage_change"] is None


def test_get_pr_details_validation():
    """Missing configs, missing PRs and non-integer PRs are rejected."""
    with pytest.raises(ValueError, match="config_name is required"):
        asyncio.run(get_pr_details("org", "repo", ["1"]))
    with pytest.raises(ValueError, match="At least one"):
        asyncio.run(get_pr_details("org", "repo", [], configs=["a.yaml"]))
    with pytest.raises(ValueError, match="must be integers"):
        asyncio.run(get_pr_details("org", "repo", ["abc"], configs=["a.yaml"]))


def test_openshift_report_on_pr(fake_orion):
    """PR analysis passes PR input vars to Orion and annotates percentage changes."""
    fake = fake_orion(pr_analysis, make_result({
        "periodic_avg": {"m": {"value": 10.0}},
        "pulls": [{"data": [{"metrics": {"m": {"value": 12.0}}}]}],
    }))

    out = asyncio.run(openshift_report_on_pr("4.22", pull_requests="11, 12", config_name="a.yaml",
                                             input_vars='{"platform": "AWS"}'))

    summary = out["summaries"][0]
    assert summary["pulls"][0]["data"][0]["metrics"]["m"]["percentage_change"] == pytest.approx(20.0)
    call = fake.calls[0]
    assert call["pull_numbers"] == [11, 12]
    assert call["pr_analysis"] is True
    assert call["input_vars"] == {
        "platform": "AWS",
        "jobtype": "pull",
        "organization": "openshift",
        "repository": "ovn-kubernetes",
        "pull_number": "11",
    }


def test_openshift_report_on_pr_errors(fake_orion):
    """Missing config and empty Orion output are reported without raising."""
    assert asyncio.run(openshift_report_on_pr("4.22")) == {"summaries": [], "error": PR_CONFIG_REQUIRED_MSG}

    fake_orion(pr_analysis, make_result("not json"))
    assert asyncio.run(openshift_report_on_pr("4.22", config_name="a.yaml")) == {
        "summaries": [],
        "message": NO_PR_DATA_MSG,
    }
