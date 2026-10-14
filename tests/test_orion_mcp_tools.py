"""
Response-shape tests for the MCP tools and resources exposed by orion_mcp.py.

Orion, Elasticsearch, and prow are all mocked: ``run_orion`` is replaced with
an AsyncMock and HTTP goes through ``httpx.MockTransport``.
"""

import asyncio
import base64
import json
import os
from datetime import datetime, timedelta, timezone
from unittest import mock

import httpx
import pytest
from mcp import types

import orion_mcp
from utils.constants import (
    DEFAULT_CONFIG,
    DEFAULT_ES_METADATA_INDEX,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_NETWORKING_CONFIGS,
    ORION_CONFIGS_PATH,
    RELEASE_DATES,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _runs(*runs):
    """Build Orion JSON output from (timestamp, {metric: value}) tuples."""
    return [
        {"timestamp": ts, "metrics": {name: {"value": val} for name, val in metrics.items()}}
        for ts, metrics in runs
    ]


def _cfg(name):
    return os.path.join(ORION_CONFIGS_PATH, name)


# ---------------------------------------------------------------------------
# Simple tools and resources
# ---------------------------------------------------------------------------

class TestSimpleTools:
    """Tools/resources that do not call Orion."""

    def test_release_dates_resource(self):
        """The release_dates resource returns the RELEASE_DATES mapping."""
        assert orion_mcp.release_dates_resource() == RELEASE_DATES

    def test_get_data_source_resource(self, es_server):
        """The get_data_source resource returns ES_SERVER."""
        assert orion_mcp.get_data_source_resource() == es_server

    def test_get_release_date_known(self):
        """A known version returns its GA date string."""
        assert asyncio.run(orion_mcp.get_release_date("4.19")) == RELEASE_DATES["4.19"]

    def test_get_release_date_default(self):
        """The default version is 4.20."""
        assert asyncio.run(orion_mcp.get_release_date()) == RELEASE_DATES["4.20"]

    def test_get_release_date_unknown(self):
        """An unknown version returns an explanatory string."""
        assert asyncio.run(orion_mcp.get_release_date("3.11")) == "Invalid version: 3.11"

    def test_get_orion_configs(self):
        """get_orion_configs lists the configs discovered at import time."""
        result = orion_mcp.get_orion_configs()
        assert result == ["cluster-density.yaml", "node-density.yaml"]
        assert all(isinstance(c, str) for c in result)


# ---------------------------------------------------------------------------
# discover_jobs
# ---------------------------------------------------------------------------

def _es_bucket(job, benchmarks, hits):
    return {
        "key": job,
        "benchmarks": {"buckets": [{"key": b} for b in benchmarks]},
        "top_hit": {"hits": {"hits": [{"_source": h} for h in hits]}},
    }


@pytest.mark.usefixtures("es_server")
class TestDiscoverJobs:
    """discover_jobs queries ES and resolves configs from prow."""

    @pytest.fixture
    def es(self, mock_async_http):
        """Serve a canned ES aggregation response and capture requests."""
        captured = []
        buckets = [
            _es_bucket(
                "periodic-ci-aws-24nodes",
                ["cluster-density-v2", "node-density"],
                [
                    {"platform": "AWS", "clusterType": "self-managed", "workerNodesCount": 24,
                     "networkType": "OVNKubernetes", "fips": False, "ipsec": True, "encrypted": "false",
                     "masterNodesType": "m6a.xlarge", "masterNodesCount": 3, "workerNodesType": "m5.2xlarge",
                     "ocpVersion": "4.22.0-0.nightly", "buildUrl": "https://prow/latest"},
                    {"buildUrl": "https://prow/older"},
                    {"platform": "AWS"},
                ],
            ),
            _es_bucket("periodic-ci-empty", [], []),
        ]

        def _handler(request):
            captured.append(request)
            return httpx.Response(200, json={"aggregations": {"jobs": {"buckets": buckets}}})

        mock_async_http(_handler)
        return captured

    def test_response_shape(self, es, monkeypatch):
        """The result maps job names to benchmarks, metadata, buildUrl, and configs."""
        resolver = mock.AsyncMock(side_effect=lambda url: [] if url == "https://prow/latest" else ["cd.yaml"])
        monkeypatch.setattr(orion_mcp, "_resolve_configs_from_prow", resolver)

        result = asyncio.run(orion_mcp.discover_jobs(version="4.22"))

        assert set(result) == {"jobs", "total"}
        assert result["total"] == 2
        job = result["jobs"]["periodic-ci-aws-24nodes"]
        assert set(job) == {"benchmarks", "metadata", "buildUrl", "configs"}
        assert job["benchmarks"] == ["cluster-density-v2", "node-density"]
        assert job["buildUrl"] == "https://prow/latest"
        # Falls through to the next build URL when the latest has no configs
        assert job["configs"] == ["cd.yaml"]
        assert job["metadata"] == {
            "platform": "AWS",
            "clusterType": "self-managed",
            "workerNodesCount": "24",
            "networkType": "OVNKubernetes",
            "fips": "False",
            "ipsec": "True",
            "encrypted": "false",
            "masterNodesType": "m6a.xlarge",
            "masterNodesCount": "3",
            "workerNodesType": "m5.2xlarge",
        }
        assert all(isinstance(v, str) for v in job["metadata"].values())

        empty = result["jobs"]["periodic-ci-empty"]
        assert empty["configs"] == []
        assert empty["buildUrl"] == ""
        assert empty["metadata"]["fips"] == "false"
        assert empty["metadata"]["workerNodesCount"] == ""

        request = es[0]
        assert str(request.url) == f"http://es.test:9200/{DEFAULT_ES_METADATA_INDEX}/_search"
        query = json.loads(request.content)
        assert query["query"]["bool"]["must"] == [
            {"term": {"jobType": "periodic"}},
            {"prefix": {"ocpVersion.keyword": "4.22"}},
        ]
        assert query["aggs"]["jobs"]["aggs"]["top_hit"]["top_hits"]["size"] == 3

    def test_filters_build_query(self, es, monkeypatch):
        """Each filter argument adds the corresponding ES clause."""
        monkeypatch.setattr(orion_mcp, "_resolve_configs_from_prow", mock.AsyncMock(return_value=[]))

        asyncio.run(orion_mcp.discover_jobs(
            platform="GCP", cluster_type="rosa-hcp", workload="payload", scale=6,
            fips="true", ipsec="false", encrypted="true", job_type="pull",
        ))

        query = json.loads(es[0].content)
        must = query["query"]["bool"]["must"]
        assert {"term": {"jobType": "pull"}} in must
        assert {"term": {"platform.keyword": "GCP"}} in must
        assert {"term": {"clusterType.keyword": "rosa-hcp"}} in must
        assert {"term": {"workerNodesCount": 6}} in must
        assert {"term": {"fips": "true"}} in must
        assert {"term": {"ipsec": "false"}} in must
        assert {"term": {"encrypted": "true"}} in must
        assert not any("prefix" in clause for clause in must)
        assert query["query"]["bool"]["filter"] == [
            {"wildcard": {"upstreamJob.keyword": {"value": "*payload*"}}}
        ]
        assert query["aggs"]["jobs"]["aggs"]["top_hit"]["top_hits"]["size"] == 20

    def test_es_failure_returns_error(self, mock_async_http):
        """ES errors are reported in an 'error' key rather than raised."""
        mock_async_http(lambda _request: httpx.Response(500, json={"error": "down"}))
        result = asyncio.run(orion_mcp.discover_jobs())
        assert set(result) == {"error"}
        assert result["error"].startswith("ES query failed")

    def test_no_aggregations(self, mock_async_http):
        """An ES response without aggregations yields zero jobs."""
        mock_async_http(lambda _request: httpx.Response(200, json={}))
        assert asyncio.run(orion_mcp.discover_jobs()) == {"jobs": {}, "total": 0}


# ---------------------------------------------------------------------------
# get_orion_metrics / get_orion_metrics_with_meta
# ---------------------------------------------------------------------------

class TestGetOrionMetrics:
    """get_orion_metrics lists metric names for a config."""

    def test_success(self, monkeypatch):
        """The orion_metrics dict is returned unchanged."""
        metrics = mock.AsyncMock(return_value={_cfg(DEFAULT_CONFIG): ["a", "b"]})
        monkeypatch.setattr(orion_mcp, "orion_metrics", metrics)

        result = asyncio.run(orion_mcp.get_orion_metrics(version="4.22", input_vars='{"platform": "AWS"}'))

        assert result == {_cfg(DEFAULT_CONFIG): ["a", "b"]}
        assert metrics.call_args.args == ([_cfg(DEFAULT_CONFIG)],)
        assert metrics.call_args.kwargs == {"version": "4.22", "input_vars": {"platform": "AWS"}}

    def test_error_string(self, monkeypatch):
        """An error string from orion_metrics is wrapped in an 'error' key."""
        monkeypatch.setattr(orion_mcp, "orion_metrics", mock.AsyncMock(return_value="boom"))
        result = asyncio.run(orion_mcp.get_orion_metrics(config_name="x.yaml"))
        assert result == {"error": "Failed to fetch Orion metrics: boom"}


class TestGetOrionMetricsWithMeta:
    """get_orion_metrics_with_meta returns metric names plus metadata."""

    def test_success(self, monkeypatch):
        """Metadata loaded from the config is returned under 'meta'."""
        loader = mock.Mock(return_value=(["m_avg"], {"m_avg": {"direction": 1, "threshold": 10.0}}))
        monkeypatch.setattr(orion_mcp, "_load_config_metrics_with_meta", loader)

        result = asyncio.run(orion_mcp.get_orion_metrics_with_meta(config_name="x.yaml", version="4.22"))

        assert result == {"metrics": ["m_avg"], "meta": {"m_avg": {"direction": 1, "threshold": 10.0}}}
        assert loader.call_args.args == (_cfg("x.yaml"),)
        assert loader.call_args.kwargs == {"version": "4.22", "input_vars": None}

    def test_falls_back_to_orion_metrics(self, monkeypatch):
        """If the config cannot be read, falls back to orion_metrics with a warning."""
        monkeypatch.setattr(orion_mcp, "_load_config_metrics_with_meta", mock.Mock(side_effect=OSError("nope")))
        monkeypatch.setattr(orion_mcp, "orion_metrics", mock.AsyncMock(return_value={"x": ["a"]}))

        result = asyncio.run(orion_mcp.get_orion_metrics_with_meta())

        assert result == {"metrics": {"x": ["a"]}, "meta": {}, "warning": "nope"}

    def test_both_fail(self, monkeypatch):
        """If both paths fail, an 'error' key combines both messages."""
        monkeypatch.setattr(orion_mcp, "_load_config_metrics_with_meta", mock.Mock(side_effect=OSError("nope")))
        monkeypatch.setattr(orion_mcp, "orion_metrics", mock.AsyncMock(return_value="orion broke"))

        assert asyncio.run(orion_mcp.get_orion_metrics_with_meta()) == {"error": "nope | orion broke"}


# ---------------------------------------------------------------------------
# openshift_report_on
# ---------------------------------------------------------------------------

class TestOpenshiftReportOn:
    """openshift_report_on returns a chart or JSON for a metric across versions."""

    METRIC = "podReadyLatency_P99"

    @pytest.fixture
    def with_data(self, run_orion_mock, make_result):
        """Make run_orion return two runs with data for METRIC."""
        run_orion_mock.return_value = make_result(stdout=_runs((1, {self.METRIC: 10}), (2, {self.METRIC: None}), (3, {self.METRIC: 12})))
        return run_orion_mock

    def test_json_single_config(self, with_data):
        """JSON output for one config is a {config, metric, lookback, display, data} object."""
        result = asyncio.run(orion_mcp.openshift_report_on(versions="4.21, 4.22", options="json"))

        assert isinstance(result, types.TextContent)
        payload = json.loads(result.text)
        assert set(payload) == {"config", "metric", "lookback", "display", "data"}
        assert payload["config"] == DEFAULT_CONFIG
        assert payload["metric"] == self.METRIC
        assert payload["lookback"] == DEFAULT_LOOKBACK_DAYS
        assert payload["display"] is None
        assert set(payload["data"]) == {"4.21", "4.22"}
        assert payload["data"]["4.22"][self.METRIC]["value"] == [10, None, 12]
        assert [c.kwargs["version"] for c in with_data.call_args_list] == ["4.21", "4.22"]

    def test_json_multiple_configs(self, with_data):
        """JSON output for several configs is wrapped in 'results'."""
        result = asyncio.run(orion_mcp.openshift_report_on(
            versions="4.22", config_name="a.yaml,b.yaml", options="json",
        ))
        payload = json.loads(result.text)
        assert set(payload) == {"results"}
        assert [r["config"] for r in payload["results"]] == ["a.yaml", "b.yaml"]
        assert [c.kwargs["config"] for c in with_data.call_args_list] == [_cfg("a.yaml"), _cfg("b.yaml")]

    def test_json_with_display_field(self, with_data):
        """'json:<field>' passes the display field through to Orion."""
        result = asyncio.run(orion_mcp.openshift_report_on(versions="4.22", options="json:ocpVirtVersion"))
        assert json.loads(result.text)["display"] == "ocpVirtVersion"
        assert with_data.call_args.kwargs["display"] == "ocpVirtVersion"

    @pytest.mark.usefixtures("with_data")
    def test_image_output(self):
        """The default output is a PNG ImageContent."""
        result = asyncio.run(orion_mcp.openshift_report_on(versions="4.22"))
        assert isinstance(result, types.ImageContent)
        assert result.mimeType == "image/png"
        assert base64.b64decode(result.data).startswith(PNG_MAGIC)

    @pytest.mark.usefixtures("with_data")
    def test_both_output(self):
        """'both' returns JSON followed by base64 image data in one TextContent."""
        result = asyncio.run(orion_mcp.openshift_report_on(versions="4.22", options="both"))
        assert isinstance(result, types.TextContent)
        json_part, image_part = result.text.split("\n\n[IMAGE_DATA_BASE64]\n")
        assert json.loads(json_part)["plot_info"] == "Image data follows JSON data"
        assert base64.b64decode(image_part).startswith(PNG_MAGIC)

    @pytest.mark.usefixtures("run_orion_mock")
    def test_no_data_returns_errors(self):
        """When no version has data, the per-version errors are returned as text."""
        result = asyncio.run(orion_mcp.openshift_report_on(versions="4.21,4.22", options="json"))
        assert isinstance(result, types.TextContent)
        assert result.text.splitlines() == [
            f"[{DEFAULT_CONFIG}] No data for version 4.21, metric {self.METRIC}",
            f"[{DEFAULT_CONFIG}] No data for version 4.22, metric {self.METRIC}",
        ]

    def test_all_none_values(self, run_orion_mock, make_result):
        """A version whose values are all None is reported as an error."""
        run_orion_mock.return_value = make_result(stdout=_runs((1, {self.METRIC: None})))
        result = asyncio.run(orion_mcp.openshift_report_on(versions="4.22"))
        assert result.text == f"[{DEFAULT_CONFIG}] All values are None for version 4.22"

    @pytest.mark.usefixtures("run_orion_mock")
    def test_malformed_input_vars_raise(self):
        """Malformed input_vars currently propagate as ValueError."""
        with pytest.raises(ValueError):
            asyncio.run(orion_mcp.openshift_report_on(versions="4.22", input_vars="{bad"))


# ---------------------------------------------------------------------------
# get_orion_performance_data
# ---------------------------------------------------------------------------

class TestGetOrionPerformanceData:
    """get_orion_performance_data returns flat metric values."""

    METRIC = "podReadyLatency_P99"

    def test_single_config_shape(self, run_orion_mock, make_result):
        """One config returns a flat dict with values and count; None values dropped."""
        run_orion_mock.return_value = make_result(stdout=_runs((1, {self.METRIC: 1.5}), (2, {self.METRIC: None}), (3, {self.METRIC: 2.5})))

        result = asyncio.run(orion_mcp.get_orion_performance_data(version="4.22", lookback="7", since="2025-01-01"))

        assert result == {
            "config": DEFAULT_CONFIG,
            "metric": self.METRIC,
            "version": "4.22",
            "lookback": "7",
            "values": [1.5, 2.5],
            "count": 2,
        }
        kwargs = run_orion_mock.call_args.kwargs
        assert kwargs["since"] == "2025-01-01"
        assert kwargs["config"] == _cfg(DEFAULT_CONFIG)

    def test_multiple_configs_shape(self, run_orion_mock, make_result):
        """Several configs return {'results': [...]} with per-config errors inline."""
        run_orion_mock.side_effect = [
            make_result(stdout=_runs((1, {self.METRIC: 3}))),
            make_result(stdout="[]", returncode=3),
            RuntimeError("orion crashed"),
        ]

        result = asyncio.run(orion_mcp.get_orion_performance_data("a.yaml,b.yaml,c.yaml"))

        assert set(result) == {"results"}
        ok, missing, crashed = result["results"]
        assert ok["config"] == "a.yaml"
        assert ok["values"] == [3]
        assert missing == {"config": "b.yaml", "error": f"No data found for metric {self.METRIC}"}
        assert crashed == {"config": "c.yaml", "error": "orion crashed"}


# ---------------------------------------------------------------------------
# get_pr_details / openshift_report_on_pr
# ---------------------------------------------------------------------------

PR_OUTPUT = {
    "periodic_avg": {"latency": {"value": 100}, "cpu": 50},
    "pulls": [{"data": [{"metrics": {"latency": {"value": 120}, "cpu": {"value": 40}}}]}],
}


class TestGetPrDetails:
    """get_pr_details runs Orion in PR-analysis mode."""

    def test_requires_configs(self):
        """Without configs, the caller is told to run discover_jobs first."""
        with pytest.raises(ValueError, match="config_name is required"):
            asyncio.run(orion_mcp.get_pr_details("o", "r", ["1"]))

    def test_requires_pull_requests(self):
        """An empty PR list is rejected."""
        with pytest.raises(ValueError, match="At least one pull request"):
            asyncio.run(orion_mcp.get_pr_details("o", "r", [], configs=["a.yaml"]))

    def test_pull_requests_must_be_integers(self):
        """Non-numeric PR identifiers are rejected."""
        with pytest.raises(ValueError, match="must be integers"):
            asyncio.run(orion_mcp.get_pr_details("o", "r", ["abc"], configs=["a.yaml"]))

    def test_summaries(self, run_orion_mock, make_result):
        """Each config produces a summary with percentage changes added."""
        run_orion_mock.return_value = make_result(stdout=PR_OUTPUT)

        summaries = asyncio.run(orion_mcp.get_pr_details(
            "openshift", "ovn-kubernetes", ["3169", "3170"], "4.22", "10",
            configs=["a.yaml"], input_vars={"platform": "AWS"},
        ))

        assert len(summaries) == 1
        summary = summaries[0]
        assert set(summary) == {"config", "periodic_avg", "pulls"}
        assert summary["config"] == _cfg("a.yaml")
        metrics = summary["pulls"][0]["data"][0]["metrics"]
        assert metrics["latency"]["percentage_change"] == pytest.approx(20.0)
        assert metrics["cpu"]["percentage_change"] == pytest.approx(-20.0)

        kwargs = run_orion_mock.call_args.kwargs
        assert kwargs["pr_analysis"] is True
        assert kwargs["pull_numbers"] == [3169, 3170]
        assert kwargs["version"] == "4.22"
        assert kwargs["lookback"] == "10"
        assert kwargs["input_vars"] == {
            "platform": "AWS",
            "jobtype": "pull",
            "organization": "openshift",
            "repository": "ovn-kubernetes",
            "pull_number": "3169",
        }

    def test_input_vars_not_mutated(self, run_orion_mock, make_result):
        """The caller's input_vars dict is copied, not mutated."""
        run_orion_mock.return_value = make_result(stdout=PR_OUTPUT)
        original = {"platform": "AWS"}
        asyncio.run(orion_mcp.get_pr_details("o", "r", ["1"], configs=["a.yaml"], input_vars=original))
        assert original == {"platform": "AWS"}

    @pytest.mark.parametrize(
        "stdout",
        ["not json", json.dumps([1, 2]), json.dumps({"pulls": []}), json.dumps({"periodic_avg": {}})],
    )
    def test_unusable_output_is_skipped(self, run_orion_mock, make_result, stdout):
        """Unparseable or incomplete Orion output produces no summary."""
        run_orion_mock.return_value = make_result(stdout=stdout)
        assert not asyncio.run(orion_mcp.get_pr_details("o", "r", ["1"], configs=["a.yaml"]))


class TestOpenshiftReportOnPr:
    """openshift_report_on_pr wraps get_pr_details in a {'summaries': ...} response."""

    def test_success(self, run_orion_mock, make_result):
        """Successful analysis returns summaries; pull_requests overrides pull_request."""
        run_orion_mock.return_value = make_result(stdout=PR_OUTPUT)

        result = asyncio.run(orion_mcp.openshift_report_on_pr(
            pull_request="1", pull_requests="3169, 3170", config_name="a.yaml",
        ))

        assert set(result) == {"summaries"}
        assert len(result["summaries"]) == 1
        assert run_orion_mock.call_args.kwargs["pull_numbers"] == [3169, 3170]

    def test_single_pull_request(self, run_orion_mock, make_result):
        """Without pull_requests, the single pull_request is analysed."""
        run_orion_mock.return_value = make_result(stdout=PR_OUTPUT)
        asyncio.run(orion_mcp.openshift_report_on_pr(pull_request="42", config_name="a.yaml"))
        assert run_orion_mock.call_args.kwargs["pull_numbers"] == [42]

    def test_requires_config(self):
        """A missing config_name returns an error response."""
        result = asyncio.run(orion_mcp.openshift_report_on_pr())
        assert result["summaries"] == []
        assert "config_name is required" in result["error"]

    def test_malformed_input_vars(self):
        """Malformed input_vars return an error response."""
        result = asyncio.run(orion_mcp.openshift_report_on_pr(config_name="a.yaml", input_vars="{bad"))
        assert result["summaries"] == []
        assert "Malformed input_vars JSON" in result["error"]

    def test_invalid_pr_number(self):
        """Non-numeric PR numbers return an error response."""
        result = asyncio.run(orion_mcp.openshift_report_on_pr(pull_request="abc", config_name="a.yaml"))
        assert result == {"summaries": [], "error": "Pull request numbers must be integers"}

    @pytest.mark.usefixtures("run_orion_mock")
    def test_no_data(self):
        """No usable output returns an empty summaries list with a message."""
        result = asyncio.run(orion_mcp.openshift_report_on_pr(config_name="a.yaml"))
        assert result["summaries"] == []
        assert "No performance data found" in result["message"]


# ---------------------------------------------------------------------------
# Regression tools
# ---------------------------------------------------------------------------

CHANGEPOINT_OUTPUT = [
    {"ocpVersion": "4.22.0-a", "prs": ["pr1"], "metrics": {}},
    {
        "is_changepoint": True,
        "ocpVersion": "4.22.0-b",
        "buildUrl": "https://prow/2",
        "prs": ["pr1", "pr2"],
        "metrics": {"podReadyLatency_P99": {"percentage_change": 15.5}},
    },
]


class TestRegressionTools:
    """has_openshift_regressed / has_networking_regressed and their helpers."""

    def test_no_changepoints(self, run_orion_mock, make_result):
        """No changepoints returns a fixed message."""
        run_orion_mock.return_value = make_result(stdout=_runs((1, {"m": 1})))
        assert asyncio.run(orion_mcp.has_openshift_regressed()) == "No changepoints found"

    def test_default_config_and_jira_ack(self, run_orion_mock):
        """The default config is used and JIRA ACKs filter to Done."""
        asyncio.run(orion_mcp.has_openshift_regressed(version="4.22", lookback="30"))
        kwargs = run_orion_mock.call_args.kwargs
        assert kwargs["config"] == _cfg(DEFAULT_CONFIG)
        assert kwargs["version"] == "4.22"
        assert kwargs["lookback"] == "30"
        assert kwargs["jira_ack"] is True
        assert kwargs["jira_status_filter"] == "Done"

    def test_changepoint_report(self, run_orion_mock, make_result):
        """A changepoint is rendered with version, build URL, PRs, and metrics."""
        run_orion_mock.return_value = make_result(stdout=CHANGEPOINT_OUTPUT)
        report = asyncio.run(orion_mcp.has_openshift_regressed(config_name="a.yaml"))
        assert report.splitlines() == [
            "⚠️ Change detected in configuration: 'a.yaml'",
            "OCP Version: 4.22.0-b",
            "Previous OCP Version: 4.22.0-a",
            "Build URL: https://prow/2",
            "PRs added since Previous OCP Version:",
            "  - pr2",
            "Affected metrics:",
            "  - podReadyLatency_P99 increased by 15.50%",
        ]

    def test_changepoint_without_new_prs(self, run_orion_mock, make_result):
        """A changepoint with no new PRs lists 'None'."""
        data = [{"is_changepoint": True, "ocpVersion": "v", "metrics": {}}]
        run_orion_mock.return_value = make_result(stdout=data)
        report = asyncio.run(orion_mcp.has_openshift_regressed())
        assert "  - None" in report.splitlines()
        assert "Build URL" not in report

    def test_orion_failure_reported(self, run_orion_mock, make_result):
        """Unparseable Orion output is reported with exit code and stderr snippet."""
        run_orion_mock.return_value = make_result(stdout="", stderr="Traceback: boom", returncode=1)
        report = asyncio.run(orion_mcp.has_openshift_regressed(config_name="a.yaml"))
        assert report == "❌ Error: Orion failed for a.yaml (exit 1): Traceback: boom"

    def test_multiple_configs(self, run_orion_mock, make_result):
        """Reports for several configs are joined; exit code 3 (no data) is skipped."""
        run_orion_mock.side_effect = [
            make_result(stdout=CHANGEPOINT_OUTPUT),
            make_result(stdout="", returncode=3),
            make_result(stdout=CHANGEPOINT_OUTPUT),
        ]
        report = asyncio.run(orion_mcp.has_openshift_regressed(config_name="a.yaml,b.yaml,c.yaml"))
        blocks = report.split("\n\n")
        assert len(blocks) == 2
        assert "'a.yaml'" in blocks[0]
        assert "'c.yaml'" in blocks[1]

    def test_malformed_input_vars(self, run_orion_mock):
        """Malformed input_vars return an error string without running Orion."""
        result = asyncio.run(orion_mcp.has_openshift_regressed(input_vars="{bad"))
        assert result.startswith("Error: Malformed input_vars JSON")
        run_orion_mock.assert_not_called()

    def test_input_vars_forwarded(self, run_orion_mock):
        """Parsed input_vars are passed to Orion."""
        asyncio.run(orion_mcp.has_openshift_regressed(input_vars='{"platform": "AWS"}'))
        assert run_orion_mock.call_args.kwargs["input_vars"] == {"platform": "AWS"}

    def test_networking_default_configs(self, run_orion_mock):
        """has_networking_regressed defaults to the networking config set."""
        result = asyncio.run(orion_mcp.has_networking_regressed())
        assert result == "No changepoints found"
        called = [c.kwargs["config"] for c in run_orion_mock.call_args_list]
        assert called == [_cfg(c) for c in DEFAULT_NETWORKING_CONFIGS]

    def test_networking_explicit_config(self, run_orion_mock):
        """An explicit config_name overrides the networking defaults."""
        asyncio.run(orion_mcp.has_networking_regressed(config_name="udn.yaml"))
        assert [c.kwargs["config"] for c in run_orion_mock.call_args_list] == [_cfg("udn.yaml")]


# ---------------------------------------------------------------------------
# metrics_correlation
# ---------------------------------------------------------------------------

class TestMetricsCorrelation:
    """metrics_correlation returns a scatter-plot image or a text error."""

    def test_image(self, run_orion_mock, make_result):
        """With both metrics present, a PNG ImageContent is returned."""
        run_orion_mock.return_value = make_result(stdout=_runs(
            (1, {"a": 1, "b": 2}), (2, {"a": 2, "b": 4}), (3, {"a": 3, "b": 7}),
        ))
        result = asyncio.run(orion_mcp.metrics_correlation(metric1="a", metric2="b", since="2025-01-01"))
        assert isinstance(result, types.ImageContent)
        assert result.mimeType == "image/png"
        assert base64.b64decode(result.data).startswith(PNG_MAGIC)
        assert run_orion_mock.call_args.kwargs["since"] == "2025-01-01"

    def test_missing_metric(self, run_orion_mock, make_result):
        """A metric absent from the summary returns a TextContent explanation."""
        run_orion_mock.return_value = make_result(stdout=_runs((1, {"a": 1})))
        result = asyncio.run(orion_mcp.metrics_correlation(metric1="a", metric2="b"))
        assert isinstance(result, types.TextContent)
        assert "not present" in result.text

    def test_orion_error(self, run_orion_mock, make_result):
        """An Orion summarization error returns a TextContent error."""
        run_orion_mock.return_value = make_result(stdout="garbage")
        result = asyncio.run(orion_mcp.metrics_correlation())
        assert isinstance(result, types.TextContent)
        assert result.text.startswith("Error processing Orion output")


# ---------------------------------------------------------------------------
# has_nightly_regressed
# ---------------------------------------------------------------------------

NIGHTLY = "4.22.0-0.nightly-2026-01-05-203335"
PREV_NIGHTLY = "4.22.0-0.nightly-2026-01-01-123456"


class TestHasNightlyRegressed:
    """has_nightly_regressed validates input and scopes analysis to the nightly."""

    def test_invalid_version(self, run_orion_mock):
        """An unparseable nightly string returns an error."""
        result = asyncio.run(orion_mcp.has_nightly_regressed("garbage"))
        assert result.startswith("Error parsing nightly version")
        run_orion_mock.assert_not_called()

    @pytest.mark.usefixtures("run_orion_mock")
    def test_ga_version_rejected(self):
        """A GA version is not accepted as a nightly."""
        assert asyncio.run(orion_mcp.has_nightly_regressed("4.22")) == "Error: '4.22' is not a nightly version."

    @pytest.mark.usefixtures("run_orion_mock")
    def test_invalid_previous(self):
        """An unparseable previous_nightly returns an error."""
        result = asyncio.run(orion_mcp.has_nightly_regressed(NIGHTLY, previous_nightly="nope"))
        assert result.startswith("Error parsing previous_nightly")

    @pytest.mark.usefixtures("run_orion_mock")
    def test_previous_ga_rejected(self):
        """A GA previous_nightly is rejected."""
        result = asyncio.run(orion_mcp.has_nightly_regressed(NIGHTLY, previous_nightly="4.21"))
        assert result == "Error: '4.21' is not a nightly version."

    @pytest.mark.usefixtures("run_orion_mock")
    def test_previous_must_be_earlier(self):
        """previous_nightly must be strictly earlier than nightly_version."""
        result = asyncio.run(orion_mcp.has_nightly_regressed(PREV_NIGHTLY, previous_nightly=NIGHTLY))
        assert result == "Error: previous_nightly must be earlier than nightly_version."

    def test_malformed_input_vars(self, run_orion_mock):
        """Malformed input_vars return an error without running Orion."""
        result = asyncio.run(orion_mcp.has_nightly_regressed(NIGHTLY, input_vars="{bad"))
        assert result.startswith("Error: Malformed input_vars JSON")
        run_orion_mock.assert_not_called()

    def test_no_data(self, run_orion_mock, make_result):
        """An empty Orion result reports no regressions; Orion runs on the major version."""
        run_orion_mock.return_value = make_result(stdout="[]")
        assert asyncio.run(orion_mcp.has_nightly_regressed(NIGHTLY)) == "No regressions found"
        kwargs = run_orion_mock.call_args.kwargs
        assert kwargs["version"] == "4.22"
        assert kwargs["jira_ack"] is True
        assert kwargs["jira_status_filter"] == "Done"

    def test_orion_failure(self, run_orion_mock, make_result):
        """Unparseable Orion output is reported per config."""
        run_orion_mock.return_value = make_result(stdout="", stderr="crash", returncode=2)
        result = asyncio.run(orion_mcp.has_nightly_regressed(NIGHTLY, config_name="a.yaml"))
        assert result == "❌ Error: Orion failed for a.yaml (exit 2): crash"

    def test_unexpected_type(self, run_orion_mock, make_result):
        """Non-list Orion output is reported per config."""
        run_orion_mock.return_value = make_result(stdout={"not": "a list"})
        result = asyncio.run(orion_mcp.has_nightly_regressed(NIGHTLY, config_name="a.yaml"))
        assert result == "❌ Error: Orion returned unexpected data type for a.yaml: dict"

    @pytest.mark.xfail(
        raises=TypeError,
        strict=True,
        reason="parse_nightly_version returns a naive datetime but parse_timestamp returns an "
               "aware one, so filter_data_by_timestamp raises when comparing them",
    )
    def test_changepoint_before_nightly(self, run_orion_mock, make_result):
        """A changepoint at or before the nightly build time is reported."""
        data = [
            {"timestamp": "2026-01-04T00:00:00Z", "ocpVersion": "a", "metrics": {}},
            {"timestamp": "2026-01-05T00:00:00Z", "ocpVersion": "b", "is_changepoint": True,
             "metrics": {"m": {"percentage_change": 10}}},
            {"timestamp": "2026-01-06T00:00:00Z", "ocpVersion": "c", "is_changepoint": True,
             "metrics": {"m": {"percentage_change": 99}}},
        ]
        run_orion_mock.return_value = make_result(stdout=data)
        result = asyncio.run(orion_mcp.has_nightly_regressed(NIGHTLY, config_name="a.yaml"))
        assert result.startswith(f"⚠️ Regression in {NIGHTLY}")
        assert "Metrics: m increased by 10.00%" in result
        assert "99.00%" not in result


# ---------------------------------------------------------------------------
# get_performance_summary
# ---------------------------------------------------------------------------

class TestGetPerformanceSummary:
    """get_performance_summary aggregates per-metric stats across configs."""

    @pytest.fixture(autouse=True)
    def loader(self, monkeypatch):
        """Patch config metric loading with three metrics (one never reported) and metadata."""
        fake = mock.Mock(return_value=(
            ["lat_P99", "cpu_avg", "absent_avg"],
            {"lat_P99": {"direction": 1, "threshold": 10.0}, "cpu_avg": {"direction": -1, "threshold": None}},
        ))
        monkeypatch.setattr(orion_mcp, "_load_config_metrics_with_meta", fake)
        return fake

    def test_summary_shape_and_stats(self, run_orion_mock, make_result):
        """Stats are computed for the current window and compared with the prior window."""
        now = datetime.now(tz=timezone.utc)
        current = _runs(
            (now.isoformat(), {"lat_P99": 10, "cpu_avg": 1}),
            (now.isoformat(), {"lat_P99": 20, "cpu_avg": None}),
        )
        older = (now - timedelta(days=20)).timestamp()
        prior = current + _runs((older, {"lat_P99": 10, "cpu_avg": 2}), (older, {"lat_P99": 10}))

        def _run(**kwargs):
            return make_result(stdout=current if kwargs["lookback"] == "14" else prior)

        run_orion_mock.side_effect = _run

        result = asyncio.run(orion_mcp.get_performance_summary(version="4.22", lookback=14, input_vars='{"a": "b"}'))

        assert set(result) == {"success", "results"}
        assert result["success"] is True
        (cfg_result,) = result["results"]
        assert set(cfg_result) == {"config", "success", "metrics"}
        assert cfg_result["config"] == DEFAULT_CONFIG
        lat, cpu = cfg_result["metrics"]
        assert lat == {
            "name": "lat_P99", "runs": 2, "min": 10, "max": 20, "avg": 15.0,
            "change_percent": 50.0, "direction": 1, "threshold": 10.0,
        }
        assert cpu["runs"] == 1
        assert cpu["change_percent"] == -50.0
        assert cpu["direction"] == -1
        lookbacks = sorted(c.kwargs["lookback"] for c in run_orion_mock.call_args_list)
        assert lookbacks == ["14", "28"]
        assert all(c.kwargs["input_vars"] == {"a": "b"} for c in run_orion_mock.call_args_list)

    def test_no_prior_data(self, run_orion_mock, make_result):
        """Without prior-window data, change_percent is None."""
        run_orion_mock.return_value = make_result(stdout=_runs((datetime.now(tz=timezone.utc).isoformat(), {"lat_P99": 5})))
        result = asyncio.run(orion_mcp.get_performance_summary())
        assert result["results"][0]["metrics"][0]["change_percent"] is None

    @pytest.mark.usefixtures("run_orion_mock")
    def test_multiple_configs(self):
        """Each config gets its own result entry; no data means success False."""
        result = asyncio.run(orion_mcp.get_performance_summary(config_name="a.yaml,b.yaml"))
        assert result["success"] is False
        assert [r["config"] for r in result["results"]] == ["a.yaml", "b.yaml"]
        assert all(r == {"config": r["config"], "success": False, "metrics": []} for r in result["results"])

    def test_config_load_failure(self, monkeypatch, run_orion_mock):
        """A config that cannot be loaded yields an error entry and Orion is not run."""
        monkeypatch.setattr(orion_mcp, "_load_config_metrics_with_meta", mock.Mock(side_effect=OSError("missing")))
        result = asyncio.run(orion_mcp.get_performance_summary(config_name="a.yaml"))
        assert result == {
            "success": False,
            "results": [{"config": "a.yaml", "success": False, "error": "Failed to load config metrics: missing"}],
        }
        run_orion_mock.assert_not_called()

    def test_orion_failure(self, run_orion_mock):
        """An exception from Orion yields an error entry."""
        run_orion_mock.side_effect = RuntimeError("podman missing")
        result = asyncio.run(orion_mcp.get_performance_summary())
        assert result["results"][0] == {"config": DEFAULT_CONFIG, "success": False, "error": "Orion failed: podman missing"}

    def test_unexpected_output(self, run_orion_mock, make_result):
        """A summarization error yields an error entry."""
        run_orion_mock.return_value = make_result(stdout="garbage")
        entry = asyncio.run(orion_mcp.get_performance_summary())["results"][0]
        assert entry["success"] is False
        assert entry["error"].startswith("Unexpected Orion output")

    def test_malformed_input_vars(self, run_orion_mock):
        """Malformed input_vars return an error response."""
        result = asyncio.run(orion_mcp.get_performance_summary(input_vars="{bad"))
        assert result["success"] is False
        assert result["results"] == []
        assert "Malformed input_vars JSON" in result["error"]
        run_orion_mock.assert_not_called()
