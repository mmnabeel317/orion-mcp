"""Unit tests for the internal helper functions in orion_mcp.py."""

import asyncio
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

import utils.utils as orion_utils
from orion_mcp import (
    _add_percentage_changes,
    _config_path,
    _extract_and_set_es_server,
    _extract_regression_details,
    _load_config_metrics_with_meta,
    _metric_key,
    _orion_error_snippet,
    _parse_input_vars,
    _render_config_yaml,
    _resolve_config_and_vars,
    _resolve_configs_from_prow,
    _split_configs,
    _timestamp_after,
)
from utils.constants import DEFAULT_CONFIG, GCSWEB_BASE_URL, ORION_CONFIGS_PATH, PROW_VIEW_PREFIX
from utils.header_decryption import HEADER_NAME


# ---------------------------------------------------------------------------
# _parse_input_vars
# ---------------------------------------------------------------------------

class TestParseInputVars:
    """_parse_input_vars() parses the JSON input_vars tool parameter."""

    @pytest.mark.parametrize("value", ["", None])
    def test_empty_returns_none(self, value):
        """Empty or missing input means 'not provided'."""
        assert _parse_input_vars(value) is None

    @pytest.mark.parametrize(
        "value, expected",
        [
            ('{"a": 1}', {"a": 1}),
            ("{}", {}),
            ('{"platform": "AWS", "workerNodesCount": 24, "fips": "false"}',
             {"platform": "AWS", "workerNodesCount": 24, "fips": "false"}),
            ('  {"nested": {"k": [1, 2]}}  ', {"nested": {"k": [1, 2]}}),
        ],
    )
    def test_valid_object(self, value, expected):
        """Valid JSON objects are returned as dicts."""
        assert _parse_input_vars(value) == expected

    @pytest.mark.parametrize("value", ["{bad", "{'a': 1}", '{"a": }', "not json", "{", '{"a": 1'])
    def test_malformed_json_raises(self, value):
        """Malformed JSON raises ValueError with a descriptive message."""
        with pytest.raises(ValueError, match="Malformed input_vars JSON"):
            _parse_input_vars(value)

    def test_malformed_error_chains_original(self):
        """The original JSONDecodeError is chained for debugging."""
        with pytest.raises(ValueError) as exc_info:
            _parse_input_vars("{bad")
        assert isinstance(exc_info.value.__cause__, json.JSONDecodeError)

    @pytest.mark.xfail(
        strict=False,
        reason="_parse_input_vars currently accepts any JSON value; non-object rejection is tracked in issue #16",
    )
    @pytest.mark.parametrize("value", ["[1]", '"hello"', "42", "true", "null"])
    def test_non_object_rejected(self, value):
        """Valid JSON that is not an object should be rejected."""
        with pytest.raises(ValueError):
            _parse_input_vars(value)


# ---------------------------------------------------------------------------
# _split_configs
# ---------------------------------------------------------------------------

class TestSplitConfigs:
    """_split_configs() turns the comma-separated config_name into a list."""

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("cluster-density.yaml", ["cluster-density.yaml"]),
            ("a.yaml,b.yaml", ["a.yaml", "b.yaml"]),
            (" a.yaml , b.yaml ,c.yaml ", ["a.yaml", "b.yaml", "c.yaml"]),
            ("a.yaml,,b.yaml,", ["a.yaml", "b.yaml"]),
            ("b.yaml,a.yaml,b.yaml", ["b.yaml", "a.yaml", "b.yaml"]),
        ],
    )
    def test_splits_and_trims(self, value, expected):
        """Names are trimmed, empty entries dropped, order and duplicates kept."""
        assert _split_configs(value) == expected

    @pytest.mark.parametrize("value", [None, ""])
    def test_empty_uses_default_config(self, value):
        """Empty input falls back to [DEFAULT_CONFIG]."""
        assert _split_configs(value) == [DEFAULT_CONFIG]

    @pytest.mark.parametrize("value", [None, ""])
    def test_empty_uses_explicit_default(self, value):
        """Empty input returns the explicitly supplied default."""
        assert _split_configs(value, default=["x.yaml", "y.yaml"]) == ["x.yaml", "y.yaml"]

    def test_explicit_empty_default_is_respected(self):
        """An explicitly empty default list stays empty."""
        assert not _split_configs(None, default=[])

    def test_explicit_default_ignored_when_value_given(self):
        """A non-empty value takes precedence over the default."""
        assert _split_configs("a.yaml", default=["x.yaml"]) == ["a.yaml"]

    @pytest.mark.xfail(
        strict=False,
        reason="whitespace/comma-only input currently returns []; default fallback is tracked in issue #20",
    )
    @pytest.mark.parametrize("value", ["   ", ",", " , , "])
    def test_whitespace_or_comma_only_uses_default(self, value):
        """Input with no real names should fall back to the default."""
        assert _split_configs(value, default=["cluster-density.yaml"]) == ["cluster-density.yaml"]


# ---------------------------------------------------------------------------
# _config_path / _orion_error_snippet / _timestamp_after
# ---------------------------------------------------------------------------

def test_config_path_joins_configs_dir():
    """_config_path() prefixes the Orion configs directory."""
    assert _config_path("cluster-density.yaml") == os.path.join(ORION_CONFIGS_PATH, "cluster-density.yaml")


def test_config_path_with_subdirectory():
    """_config_path() preserves relative subdirectories."""
    assert _config_path("sub/x.yaml") == os.path.join(ORION_CONFIGS_PATH, "sub", "x.yaml")


class TestOrionErrorSnippet:
    """_orion_error_snippet() extracts a short message from an Orion result."""

    def test_prefers_stderr(self):
        """stderr is preferred over stdout."""
        result = SimpleNamespace(stderr="boom", stdout="ignored")
        assert _orion_error_snippet(result) == "boom"

    def test_falls_back_to_stdout(self):
        """stdout is used when stderr is empty."""
        result = SimpleNamespace(stderr="", stdout="  out msg \n")
        assert _orion_error_snippet(result) == "out msg"

    def test_none_streams(self):
        """None streams produce an empty string."""
        assert _orion_error_snippet(SimpleNamespace(stderr=None, stdout=None)) == ""

    def test_truncated_to_200_chars(self):
        """Long output is truncated to 200 characters."""
        result = SimpleNamespace(stderr="x" * 500, stdout="")
        assert _orion_error_snippet(result) == "x" * 200

    def test_strip_happens_after_truncation(self):
        """Whitespace is stripped from the truncated snippet."""
        result = SimpleNamespace(stderr=" " * 10 + "y" * 300, stdout="")
        assert _orion_error_snippet(result) == "y" * 190


class TestTimestampAfter:
    """_timestamp_after() is a strict 'later than cutoff' comparison."""

    CUTOFF = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    def test_after(self):
        """A later timestamp is after the cutoff."""
        assert _timestamp_after("2025-01-15T12:00:01Z", self.CUTOFF)

    def test_equal_is_not_after(self):
        """A timestamp equal to the cutoff is not after it."""
        assert not _timestamp_after(self.CUTOFF.timestamp(), self.CUTOFF)

    def test_before(self):
        """An earlier timestamp is not after the cutoff."""
        assert not _timestamp_after("2025-01-14T00:00:00", self.CUTOFF)

    def test_unparseable(self):
        """Unparseable timestamps are never after the cutoff."""
        assert not _timestamp_after("garbage", self.CUTOFF)


# ---------------------------------------------------------------------------
# _metric_key
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "metric, expected",
    [
        ({"name": "ovnCPU", "agg": {"agg_type": "avg"}}, "ovnCPU_avg"),
        ({"name": "podReadyLatency", "metric_of_interest": "P99"}, "podReadyLatency_P99"),
        ({"name": "x", "agg": {"agg_type": ""}, "metric_of_interest": "P50"}, "x_P50"),
        ({"name": "x", "agg": "not-a-dict", "metric_of_interest": "P50"}, "x_P50"),
        ({"name": "x"}, "x_value"),
        ({}, "unknown_value"),
    ],
)
def test_metric_key(metric, expected):
    """_metric_key() builds the metric name used in Orion output."""
    assert _metric_key(metric) == expected


# ---------------------------------------------------------------------------
# _add_percentage_changes
# ---------------------------------------------------------------------------

class TestAddPercentageChanges:
    """_add_percentage_changes() annotates PR metrics against the periodic baseline."""

    @staticmethod
    def _pulls(metrics):
        return [{"data": [{"metrics": {name: {"value": val} for name, val in metrics.items()}}]}]

    def _changes(self, pulls):
        return {
            name: data["percentage_change"]
            for name, data in pulls[0]["data"][0]["metrics"].items()
        }

    def test_dict_and_scalar_baselines(self):
        """Baselines may be {'value': x} dicts or bare numbers."""
        pulls = self._pulls({"a": 110, "b": 45})
        _add_percentage_changes(pulls, {"a": {"value": 100}, "b": 50})
        changes = self._changes(pulls)
        assert changes["a"] == pytest.approx(10.0)
        assert changes["b"] == pytest.approx(-10.0)

    @pytest.mark.parametrize(
        "pull_value, baseline",
        [
            (10, {}),                      # metric missing from baseline
            (10, {"m": 0}),                # zero baseline
            (10, {"m": {"value": None}}),  # baseline value missing
            (None, {"m": 5}),              # pull value missing
            ("10", {"m": 5}),              # non-numeric pull value
            (10, {"m": "5"}),              # non-numeric baseline
        ],
    )
    def test_uncomputable_changes_are_none(self, pull_value, baseline):
        """When a change cannot be computed, percentage_change is None."""
        pulls = self._pulls({"m": pull_value})
        _add_percentage_changes(pulls, baseline)
        assert self._changes(pulls)["m"] is None

    def test_handles_missing_containers(self):
        """Pull objects without data/metrics are tolerated."""
        pulls = [{}, {"data": [{}]}]
        _add_percentage_changes(pulls, {"m": 1})
        assert pulls == [{}, {"data": [{}]}]


# ---------------------------------------------------------------------------
# _extract_regression_details
# ---------------------------------------------------------------------------

class TestExtractRegressionDetails:
    """_extract_regression_details() summarises changepoints from Orion output."""

    def test_non_list_returns_empty(self):
        """A non-list document yields no details."""
        assert not _extract_regression_details(json.dumps({"a": 1}))

    def test_no_changepoints(self):
        """Runs without is_changepoint yield no details."""
        data = [{"is_changepoint": False, "metrics": {}}, {"metrics": {}}]
        assert not _extract_regression_details(json.dumps(data))

    def test_changepoint_details(self):
        """Changepoint entries expose version, previous version, new PRs, and metric deltas."""
        data = [
            {"ocpVersion": "4.20.0-a", "prs": ["pr1", "pr2"], "metrics": {}},
            {
                "is_changepoint": True,
                "ocpVersion": "4.20.0-b",
                "buildUrl": "https://prow/build/2",
                "prs": ["pr2", "pr3"],
                "metrics": {
                    "up": {"percentage_change": 12.345},
                    "down": {"percentage_change": -5},
                    "flat": {"percentage_change": 0},
                    "missing": {},
                },
            },
        ]
        details = _extract_regression_details(json.dumps(data))
        assert details == [{
            "buildUrl": "https://prow/build/2",
            "ocpVersion": "4.20.0-b",
            "previousOcpVersion": "4.20.0-a",
            "prs_added": ["pr3"],
            "metrics": ["up increased by 12.35%", "down decreased by 5.00%"],
        }]

    def test_first_entry_changepoint_has_no_previous(self):
        """A changepoint at index 0 has no previous version and all PRs are new."""
        data = [{"is_changepoint": True, "ocpVersion": "v1", "prs": ["a"], "metrics": {"m": {"percentage_change": 1}}}]
        details = _extract_regression_details(json.dumps(data))
        assert details[0]["previousOcpVersion"] is None
        assert details[0]["prs_added"] == ["a"]
        assert details[0]["buildUrl"] is None

    def test_null_prs_treated_as_empty(self):
        """prs: null is treated as an empty list."""
        data = [{"prs": None}, {"is_changepoint": True, "prs": None, "metrics": {}}]
        assert _extract_regression_details(json.dumps(data))[0]["prs_added"] == []

    def test_invalid_json_raises(self):
        """Non-JSON stdout raises JSONDecodeError for callers to handle."""
        with pytest.raises(json.JSONDecodeError):
            _extract_regression_details("not json")


# ---------------------------------------------------------------------------
# _render_config_yaml / _load_config_metrics_with_meta
# ---------------------------------------------------------------------------

class TestRenderConfigYaml:
    """_render_config_yaml() renders Jinja templates in Orion config files."""

    def test_renders_version_and_input_vars(self, tmp_path):
        """version and input_vars are available to the template."""
        cfg = tmp_path / "c.yaml"
        cfg.write_text("version: '{{ version }}'\nplatform: '{{ platform }}'\njobtype: '{{ jobtype }}'\n")
        rendered = _render_config_yaml(str(cfg), "4.22", input_vars={"platform": "AWS"})
        assert rendered == {"version": "4.22", "platform": "AWS", "jobtype": "periodic"}

    def test_accepts_json_string_input_vars(self, tmp_path):
        """input_vars may also be passed as a JSON string."""
        cfg = tmp_path / "c.yaml"
        cfg.write_text("nodes: '{{ workerNodesCount }}'\n")
        assert _render_config_yaml(str(cfg), input_vars='{"workerNodesCount": 24}') == {"nodes": "24"}

    def test_env_vars_are_lowercased(self, tmp_path, monkeypatch):
        """Environment variables are exposed to the template in lowercase."""
        monkeypatch.setenv("MY_TEMPLATE_VAR", "from-env")
        cfg = tmp_path / "c.yaml"
        cfg.write_text("v: '{{ my_template_var }}'\n")
        assert _render_config_yaml(str(cfg)) == {"v": "from-env"}

    def test_falsy_defaults_do_not_override_env(self, tmp_path, monkeypatch):
        """Empty defaults (e.g. organization) do not clobber env values."""
        monkeypatch.setenv("ORGANIZATION", "openshift")
        cfg = tmp_path / "c.yaml"
        cfg.write_text("org: '{{ organization }}'\nversion: '{{ version }}'\n")
        assert _render_config_yaml(str(cfg)) == {"org": "openshift", "version": ""}

    def test_undefined_variables_render_empty(self, tmp_path):
        """Undefined variables fall back to lenient rendering instead of failing."""
        cfg = tmp_path / "c.yaml"
        cfg.write_text("v: '{{ definitely_not_defined_anywhere }}'\n")
        assert _render_config_yaml(str(cfg)) == {"v": ""}

    def test_missing_file_raises(self, tmp_path):
        """A missing config file raises an OSError."""
        with pytest.raises(OSError):
            _render_config_yaml(str(tmp_path / "missing.yaml"))


class TestLoadConfigMetricsWithMeta:
    """_load_config_metrics_with_meta() extracts metric names and metadata."""

    def test_inline_and_metrics_file(self, tmp_path):
        """Metrics come from inline tests and from referenced metricsFile entries."""
        (tmp_path / "top-metrics.yaml").write_text(
            "- name: topMetric\n  metric_of_interest: value\n  direction: -1\n"
        )
        (tmp_path / "test-metrics.yaml").write_text(
            "metrics:\n  - name: fileMetric\n    agg:\n      agg_type: max\n    threshold: '5.5'\n  - not-a-dict\n"
        )
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "metricsFile: top-metrics.yaml\n"
            "tests:\n"
            "  - name: test1\n"
            "    metricsFile: test-metrics.yaml\n"
            "    metrics:\n"
            "      - name: podReadyLatency\n"
            "        metric_of_interest: P99\n"
            "        direction: 1\n"
            "        threshold: 10\n"
            "      - name: ovnCPU\n"
            "        agg:\n"
            "          agg_type: avg\n"
            "        direction: up\n"
            "        threshold: high\n"
            "      - name: jobConfig\n"
            "        type: metadata\n"
            "  - name: test2\n"
            "    metricsFile: missing-metrics.yaml\n"
        )

        metrics, meta = _load_config_metrics_with_meta(str(cfg), "4.22")

        assert metrics == ["topMetric_value", "fileMetric_max", "podReadyLatency_P99", "ovnCPU_avg"]
        assert meta["topMetric_value"] == {
            "direction": -1, "threshold": None, "metric_of_interest": "value", "agg_type": None,
        }
        assert meta["fileMetric_max"] == {
            "direction": None, "threshold": 5.5, "metric_of_interest": None, "agg_type": "max",
        }
        assert meta["podReadyLatency_P99"] == {
            "direction": 1, "threshold": 10.0, "metric_of_interest": "P99", "agg_type": None,
        }
        assert meta["ovnCPU_avg"]["direction"] is None
        assert meta["ovnCPU_avg"]["threshold"] is None
        assert "jobConfig_value" not in meta

    def test_templated_config(self, tmp_path):
        """Templated configs are rendered with input_vars before parsing."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "tests:\n  - name: t\n    metrics:\n      - name: '{{ prefix }}Latency'\n        metric_of_interest: P50\n"
        )
        metrics, _ = _load_config_metrics_with_meta(str(cfg), input_vars={"prefix": "pod"})
        assert metrics == ["podLatency_P50"]

    def test_no_tests(self, tmp_path):
        """A config without tests yields no metrics."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("name: empty\n")
        assert _load_config_metrics_with_meta(str(cfg)) == ([], {})


# ---------------------------------------------------------------------------
# _extract_and_set_es_server / _resolve_config_and_vars
# ---------------------------------------------------------------------------

def _ctx_with_headers(headers):
    return SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers=headers)))


class TestExtractAndSetEsServer:
    """_extract_and_set_es_server() copies ES config from headers into the context var."""

    def test_none_ctx_is_noop(self):
        """A missing MCP context leaves the context var untouched."""
        _extract_and_set_es_server(None)
        assert orion_utils.current_es_config.get() is None

    def test_sets_context_from_encrypted_header(self, symmetric_key, encrypt):
        """A valid encrypted header populates current_es_config."""
        blob = encrypt({"es_server": "https://hdr", "es_metadata_index": "m*", "secret": "x"}, symmetric_key)
        _extract_and_set_es_server(_ctx_with_headers({HEADER_NAME: blob}))
        assert orion_utils.current_es_config.get() == {"es_server": "https://hdr", "es_metadata_index": "m*"}

    def test_no_header_leaves_context_unset(self):
        """Requests without the header fall back to env vars."""
        _extract_and_set_es_server(_ctx_with_headers({"Accept": "*/*"}))
        assert orion_utils.current_es_config.get() is None

    @pytest.mark.usefixtures("symmetric_key")
    def test_invalid_header_is_swallowed(self):
        """Decryption failures are logged and ignored, not raised."""
        _extract_and_set_es_server(_ctx_with_headers({HEADER_NAME: "not-valid"}))
        assert orion_utils.current_es_config.get() is None

    @pytest.mark.parametrize(
        "ctx",
        [
            SimpleNamespace(),
            SimpleNamespace(request_context=None),
            SimpleNamespace(request_context=SimpleNamespace(request=None)),
        ],
    )
    def test_ctx_without_request(self, ctx):
        """Contexts without an HTTP request (e.g. stdio transport) are ignored."""
        _extract_and_set_es_server(ctx)
        assert orion_utils.current_es_config.get() is None


class TestResolveConfigAndVars:
    """_resolve_config_and_vars() applies config default and parses input_vars."""

    def test_defaults(self):
        """No config and no input_vars resolves to DEFAULT_CONFIG and None."""
        assert asyncio.run(_resolve_config_and_vars(None, None, "4.20")) == (DEFAULT_CONFIG, None)

    def test_explicit_values(self):
        """Explicit config and input_vars are passed through/parsed."""
        result = asyncio.run(_resolve_config_and_vars(None, "x.yaml", "4.20", '{"a": "b"}'))
        assert result == ("x.yaml", {"a": "b"})

    def test_malformed_input_vars_raise(self):
        """Malformed input_vars propagate as ValueError."""
        with pytest.raises(ValueError):
            asyncio.run(_resolve_config_and_vars(None, None, "4.20", "{bad"))


# ---------------------------------------------------------------------------
# _resolve_configs_from_prow
# ---------------------------------------------------------------------------

BUILD_PATH = "test-platform-results/logs/periodic-job/123"
BUILD_URL = f"{PROW_VIEW_PREFIX}{BUILD_PATH}"
GCS_BASE = f"{GCSWEB_BASE_URL}/{BUILD_PATH}"


def _dir_listing(*names):
    return "".join(f'<a href="x"> {name}/</a>' for name in names)


def _prow_handler(pages):
    """Build an httpx handler serving the given {url: (status, text)} pages."""
    def _handler(request):
        status, text = pages.get(str(request.url), (404, "not found"))
        return httpx.Response(status, text=text)
    return _handler


class TestResolveConfigsFromProw:
    """_resolve_configs_from_prow() walks prow artifacts to find ORION_CONFIG values."""

    @pytest.mark.parametrize("url", ["", None, "https://example.com/not/prow"])
    def test_non_prow_url(self, url):
        """Missing or non-prow URLs resolve to no configs without any HTTP call."""
        assert not asyncio.run(_resolve_configs_from_prow(url))

    def test_resolves_configs(self, mock_async_http):
        """ORION_CONFIG values are collected from each orion step's build log."""
        mock_async_http(_prow_handler({
            f"{GCS_BASE}/artifacts/": (200, _dir_listing("build-resources", "release", "my-test")),
            f"{GCS_BASE}/artifacts/my-test/": (200, _dir_listing(
                "openshift-qe-orion-cd", "openshift-qe-orion-nd", "openshift-qe-orion-none",
                "openshift-qe-orion-missing", "other-step",
            )),
            f"{GCS_BASE}/artifacts/my-test/openshift-qe-orion-cd/build-log.txt":
                (200, "foo\nORION_CONFIG=examples/cluster-density.yaml\nbar"),
            f"{GCS_BASE}/artifacts/my-test/openshift-qe-orion-nd/build-log.txt":
                (200, "ORION_CONFIG=examples/node-density.yaml"),
            f"{GCS_BASE}/artifacts/my-test/openshift-qe-orion-none/build-log.txt":
                (200, "no config here"),
        }))
        assert asyncio.run(_resolve_configs_from_prow(BUILD_URL)) == [
            "cluster-density.yaml", "node-density.yaml",
        ]

    def test_artifacts_not_found(self, mock_async_http):
        """Expired artifacts resolve to no configs."""
        mock_async_http(_prow_handler({}))
        assert not asyncio.run(_resolve_configs_from_prow(BUILD_URL))

    def test_no_test_dirs(self, mock_async_http):
        """Artifacts with only build-resources/release resolve to no configs."""
        mock_async_http(_prow_handler({
            f"{GCS_BASE}/artifacts/": (200, _dir_listing("build-resources", "release")),
        }))
        assert not asyncio.run(_resolve_configs_from_prow(BUILD_URL))

    def test_test_dir_not_found(self, mock_async_http):
        """A missing test directory listing resolves to no configs."""
        mock_async_http(_prow_handler({
            f"{GCS_BASE}/artifacts/": (200, _dir_listing("my-test")),
        }))
        assert not asyncio.run(_resolve_configs_from_prow(BUILD_URL))

    def test_no_orion_steps(self, mock_async_http):
        """A test without openshift-qe-orion-* steps resolves to no configs."""
        mock_async_http(_prow_handler({
            f"{GCS_BASE}/artifacts/": (200, _dir_listing("my-test")),
            f"{GCS_BASE}/artifacts/my-test/": (200, _dir_listing("ipi-install", "gather")),
        }))
        assert not asyncio.run(_resolve_configs_from_prow(BUILD_URL))

    def test_transport_error(self, mock_async_http):
        """Network errors are logged and resolve to no configs."""
        def _raise(request):
            raise httpx.ConnectError("unreachable", request=request)
        mock_async_http(_raise)
        assert not asyncio.run(_resolve_configs_from_prow(BUILD_URL))

    def test_step_log_error_is_ignored(self, mock_async_http):
        """A failure fetching one step log does not discard other steps."""
        pages = {
            f"{GCS_BASE}/artifacts/": (200, _dir_listing("my-test")),
            f"{GCS_BASE}/artifacts/my-test/": (200, _dir_listing("openshift-qe-orion-a", "openshift-qe-orion-b")),
            f"{GCS_BASE}/artifacts/my-test/openshift-qe-orion-b/build-log.txt":
                (200, "ORION_CONFIG=examples/b.yaml"),
        }

        def _handler(request):
            if request.url.path.endswith("openshift-qe-orion-a/build-log.txt"):
                raise httpx.ReadTimeout("slow", request=request)
            return _prow_handler(pages)(request)

        mock_async_http(_handler)
        assert asyncio.run(_resolve_configs_from_prow(BUILD_URL)) == ["b.yaml"]
