"""Unit tests for utils/utils.py helper functions."""

import asyncio
import base64
import json
import math
from datetime import datetime, timedelta, timezone
from unittest import mock

import httpx
import pytest

import utils.utils as orion_utils
from utils.constants import (
    DEFAULT_ES_BENCHMARK_INDEX,
    DEFAULT_ES_METADATA_INDEX,
    DEFAULT_LOOKBACK_DAYS,
    GITHUB_CONFIGS_URL,
)
from utils.utils import (
    NightlyVersionInfo,
    compute_correlation,
    filter_data_by_timestamp,
    generate_correlation_plot,
    generate_multi_line_plot,
    get_data_source,
    get_es_benchmark_index,
    get_es_metadata_index,
    list_orion_configs,
    orion_configs,
    orion_metrics,
    parse_nightly_version,
    parse_timestamp,
    resolve_env_var,
    run_command_async,
    run_orion,
    summarize_result,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _orion_runs(*runs):
    """Build Orion JSON output from (timestamp, {metric: value}) tuples."""
    return [
        {"timestamp": ts, "metrics": {name: {"value": val} for name, val in metrics.items()}}
        for ts, metrics in runs
    ]


# ---------------------------------------------------------------------------
# resolve_env_var
# ---------------------------------------------------------------------------

class TestResolveEnvVar:
    """resolve_env_var() precedence: primary, then secondary, then default."""

    def test_prefers_primary(self, monkeypatch):
        """The primary (lowercase) variable wins when both are set."""
        monkeypatch.setenv("my_var", "primary")
        monkeypatch.setenv("MY_VAR", "secondary")
        assert resolve_env_var("my_var", "MY_VAR", "default") == "primary"

    def test_falls_back_to_secondary(self, monkeypatch):
        """The secondary variable is used when the primary is unset."""
        monkeypatch.delenv("my_var", raising=False)
        monkeypatch.setenv("MY_VAR", "secondary")
        assert resolve_env_var("my_var", "MY_VAR", "default") == "secondary"

    def test_blank_primary_is_ignored(self, monkeypatch):
        """A whitespace-only primary value is treated as unset."""
        monkeypatch.setenv("my_var", "   ")
        monkeypatch.setenv("MY_VAR", "secondary")
        assert resolve_env_var("my_var", "MY_VAR", "default") == "secondary"

    def test_default_when_neither_set(self, monkeypatch):
        """The default is returned when neither variable has a value."""
        monkeypatch.delenv("my_var", raising=False)
        monkeypatch.setenv("MY_VAR", "")
        assert resolve_env_var("my_var", "MY_VAR", "default") == "default"


# ---------------------------------------------------------------------------
# run_command_async
# ---------------------------------------------------------------------------

def _fake_process(stdout=b"", stderr=b"", returncode=0):
    """Minimal stand-in for asyncio.subprocess.Process."""
    return mock.Mock(returncode=returncode, communicate=mock.AsyncMock(return_value=(stdout, stderr)))


class TestRunCommandAsync:
    """run_command_async() wraps asyncio subprocess APIs."""

    def test_exec_returns_completed_process(self, monkeypatch):
        """stdout/stderr are decoded and returncode is propagated."""
        spawn = mock.AsyncMock(return_value=_fake_process(b"out", b"err", 2))
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

        result = asyncio.run(run_command_async(["tool", "--flag"], cwd="/tmp"))

        assert result.args == ["tool", "--flag"]
        assert result.returncode == 2
        assert result.stdout == "out"
        assert result.stderr == "err"
        args, kwargs = spawn.call_args
        assert args == ("tool", "--flag")
        assert kwargs["cwd"] == "/tmp"
        assert kwargs["env"] is None

    def test_env_is_merged_with_os_environ(self, monkeypatch):
        """Extra env vars are layered on top of the current environment."""
        monkeypatch.setenv("EXISTING_VAR", "kept")
        spawn = mock.AsyncMock(return_value=_fake_process())
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

        asyncio.run(run_command_async(["tool"], env={"NEW_VAR": "added"}))

        env = spawn.call_args.kwargs["env"]
        assert env["EXISTING_VAR"] == "kept"
        assert env["NEW_VAR"] == "added"

    def test_shell_mode_uses_shell_spawn(self, monkeypatch):
        """shell=True dispatches to create_subprocess_shell with the string."""
        spawn = mock.AsyncMock(return_value=_fake_process(b"hi"))
        monkeypatch.setattr(asyncio, "create_subprocess_shell", spawn)

        result = asyncio.run(run_command_async("echo hi", shell=True))

        assert result.stdout == "hi"
        assert spawn.call_args.args == ("echo hi",)

    def test_shell_mode_requires_string(self):
        """shell=True with a list command raises TypeError."""
        with pytest.raises(TypeError, match="must be a string"):
            asyncio.run(run_command_async(["echo", "hi"], shell=True))

    def test_exec_mode_requires_list(self):
        """shell=False with a string command raises TypeError."""
        with pytest.raises(TypeError, match="must be a list"):
            asyncio.run(run_command_async("echo hi"))

    def test_os_error_is_converted_to_failed_result(self, monkeypatch):
        """OSError while spawning yields returncode 1 with the error in stderr."""
        spawn = mock.AsyncMock(side_effect=FileNotFoundError("no such binary"))
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

        result = asyncio.run(run_command_async(["missing-binary"]))

        assert result.returncode == 1
        assert result.stdout == ""
        assert "no such binary" in result.stderr


# ---------------------------------------------------------------------------
# orion_configs
# ---------------------------------------------------------------------------

def test_orion_configs_returns_basenames():
    """orion_configs() strips directories, keeping only file names."""
    configs = ["/orion/examples/a.yaml", "b.yaml", "sub/dir/c.yaml"]
    assert orion_configs(configs) == ["a.yaml", "b.yaml", "c.yaml"]


def test_orion_configs_empty():
    """An empty input list yields an empty output list."""
    assert not orion_configs([])


# ---------------------------------------------------------------------------
# run_orion
# ---------------------------------------------------------------------------

class TestRunOrion:
    """run_orion() builds the Orion command line and environment."""

    @pytest.fixture
    def captured(self, monkeypatch, make_result):
        """Patch run_command_async and return the AsyncMock for inspection."""
        runner = mock.AsyncMock(return_value=make_result(stdout="[]"))
        monkeypatch.setattr(orion_utils, "run_command_async", runner)
        monkeypatch.setattr(orion_utils.shutil, "which", lambda _name: "/usr/bin/orion")
        return runner

    def test_basic_command_and_env(self, captured, es_server):
        """Minimal invocation builds the expected command and env."""
        result = asyncio.run(run_orion("/cfg/cd.yaml", "4.20", "15"))

        assert result.stdout == "[]"
        command = captured.call_args.args[0]
        assert command == [
            "orion", "--lookback", "15d", "--hunter-analyze",
            "--config", "/cfg/cd.yaml", "-o", "json",
        ]
        env = captured.call_args.kwargs["env"]
        assert env == {
            "ES_SERVER": es_server,
            "version": "4.20",
            "es_metadata_index": DEFAULT_ES_METADATA_INDEX,
            "es_benchmark_index": DEFAULT_ES_BENCHMARK_INDEX,
        }
        assert captured.call_args.kwargs["cwd"]

    @pytest.mark.usefixtures("es_server")
    def test_optional_flags(self, captured):
        """All optional arguments are translated into CLI flags."""
        asyncio.run(run_orion(
            "/cfg/cd.yaml", "4.20", "7",
            since="2025-01-01",
            input_vars={"platform": "AWS"},
            display="  ocpVirtVersion ",
            pr_analysis=True,
            pull_numbers=[101, 202],
            jira_ack=True,
            jira_status_filter="Done",
        ))

        command = captured.call_args.args[0]
        assert "--jira-ack" in command
        assert command[command.index("--jira-status-filter") + 1] == "Done"
        assert command[command.index("--since") + 1] == "2025-01-01"
        assert json.loads(command[command.index("--input-vars") + 1]) == {"platform": "AWS"}
        assert command[command.index("--display") + 1] == "ocpVirtVersion"
        assert "--pr-analysis" in command
        assert command.count("--pull-number") == 2
        assert command[-4:] == ["--pull-number", "101", "--pull-number", "202"]

    @pytest.mark.usefixtures("es_server")
    def test_blank_display_is_omitted(self, captured):
        """A whitespace-only display value does not add --display."""
        asyncio.run(run_orion("/cfg/cd.yaml", "4.20", "15", display="   "))
        assert "--display" not in captured.call_args.args[0]

    @pytest.mark.usefixtures("es_server")
    def test_uses_podman_when_orion_not_on_path(self, captured, monkeypatch):
        """Falls back to running Orion through podman when not installed."""
        monkeypatch.setattr(orion_utils.shutil, "which", lambda _name: None)
        asyncio.run(run_orion("/cfg/cd.yaml", "4.20", "15"))
        command = captured.call_args.args[0]
        assert command[:4] == ["podman", "run", "--env-host", "orion"]
        assert "--hunter-analyze" in command

    def test_indices_from_context(self, captured):
        """ES server and indices from the request context override env/defaults."""
        orion_utils.current_es_config.set({
            "es_server": "https://ctx-es",
            "es_metadata_index": "ctx-meta*",
            "es_benchmark_index": "ctx-bench*",
        })
        asyncio.run(run_orion("/cfg/cd.yaml", "4.20", "15"))
        env = captured.call_args.kwargs["env"]
        assert env["ES_SERVER"] == "https://ctx-es"
        assert env["es_metadata_index"] == "ctx-meta*"
        assert env["es_benchmark_index"] == "ctx-bench*"

    def test_missing_es_server_raises(self, captured):
        """Without ES_SERVER, run_orion raises and never spawns Orion."""
        with pytest.raises(EnvironmentError):
            asyncio.run(run_orion("/cfg/cd.yaml", "4.20", "15"))
        captured.assert_not_called()

    def test_empty_es_server_raises_value_error(self, captured, monkeypatch):
        """An empty ES_SERVER is rejected with ValueError."""
        monkeypatch.setenv("ES_SERVER", "")
        with pytest.raises(ValueError, match="Data source is not set"):
            asyncio.run(run_orion("/cfg/cd.yaml", "4.20", "15"))
        captured.assert_not_called()


# ---------------------------------------------------------------------------
# summarize_result
# ---------------------------------------------------------------------------

class TestSummarizeResult:
    """summarize_result() converts Orion JSON output into per-metric lists."""

    def test_returncode_3_means_no_data(self, make_result):
        """Orion exit code 3 (no data) yields an empty dict even if stdout has metrics."""
        runs = _orion_runs((100, {"a": 1}))
        assert asyncio.run(summarize_result(make_result(stdout=runs, returncode=3))) == {}
        # Same stdout with a success exit code is summarized, proving the exit code drives the result.
        assert asyncio.run(summarize_result(make_result(stdout=runs, returncode=0)))["a"] == {"value": [1]}

    def test_empty_list(self, make_result):
        """An empty JSON list yields an empty dict."""
        assert asyncio.run(summarize_result(make_result(stdout="[]"))) == {}

    def test_collects_values_per_metric(self, make_result):
        """Values are accumulated per metric across runs; raw runs are kept."""
        runs = _orion_runs((100, {"a": 1, "b": 10}), (200, {"a": 2, "b": 20}))
        summary = asyncio.run(summarize_result(make_result(stdout=runs)))
        assert summary["runs"] == runs
        assert summary["timestamp"] == 200
        assert summary["a"] == {"value": [1, 2]}
        assert summary["b"] == {"value": [10, 20]}

    def test_isolate_keeps_only_requested_metric(self, make_result):
        """With isolate set, only that metric is summarized."""
        runs = _orion_runs((100, {"a": 1, "b": 10}), (200, {"a": 2, "b": 20}))
        summary = asyncio.run(summarize_result(make_result(stdout=runs), isolate="b"))
        assert summary["b"] == {"value": [10, 20]}
        assert "a" not in summary

    def test_invalid_json_returns_error_string(self, make_result):
        """Unparseable stdout is reported as an error string, not raised."""
        summary = asyncio.run(summarize_result(make_result(stdout="not json")))
        assert isinstance(summary, str)
        assert summary.startswith("Error :")

    def test_missing_metrics_key_returns_error_string(self, make_result):
        """Runs missing the 'metrics' key are reported as an error string."""
        summary = asyncio.run(summarize_result(make_result(stdout=[{"timestamp": 1}])))
        assert isinstance(summary, str)
        assert "metrics" in summary


# ---------------------------------------------------------------------------
# get_data_source / get_es_metadata_index / get_es_benchmark_index
# ---------------------------------------------------------------------------

class TestDataSource:
    """ES connection resolution: request context first, then environment."""

    def test_from_env(self, es_server):
        """ES_SERVER from the environment is returned."""
        assert get_data_source() == es_server

    @pytest.mark.usefixtures("es_server")
    def test_from_context_overrides_env(self):
        """A context-provided es_server wins over the environment."""
        orion_utils.current_es_config.set({"es_server": "https://from-header"})
        assert get_data_source() == "https://from-header"

    def test_context_without_es_server_falls_back(self, es_server):
        """A context dict lacking es_server falls back to the environment."""
        orion_utils.current_es_config.set({"es_metadata_index": "x"})
        assert get_data_source() == es_server

    def test_missing_raises(self):
        """No context and no env var raises EnvironmentError."""
        with pytest.raises(EnvironmentError, match="ES_SERVER"):
            get_data_source()

    def test_metadata_index_default(self):
        """The metadata index falls back to the default pattern."""
        assert get_es_metadata_index() == DEFAULT_ES_METADATA_INDEX

    def test_metadata_index_env(self, monkeypatch):
        """The uppercase env var is honoured for the metadata index."""
        monkeypatch.setenv("ES_METADATA_INDEX", "custom-meta*")
        assert get_es_metadata_index() == "custom-meta*"

    def test_metadata_index_context(self, monkeypatch):
        """The context value overrides the env var for the metadata index."""
        monkeypatch.setenv("es_metadata_index", "env-meta*")
        orion_utils.current_es_config.set({"es_metadata_index": "ctx-meta*"})
        assert get_es_metadata_index() == "ctx-meta*"

    def test_benchmark_index_default(self):
        """The benchmark index falls back to the default pattern."""
        assert get_es_benchmark_index() == DEFAULT_ES_BENCHMARK_INDEX

    def test_benchmark_index_env(self, monkeypatch):
        """The lowercase env var is honoured for the benchmark index."""
        monkeypatch.setenv("es_benchmark_index", "custom-bench*")
        assert get_es_benchmark_index() == "custom-bench*"

    def test_benchmark_index_context(self):
        """The context value is used for the benchmark index."""
        orion_utils.current_es_config.set({"es_benchmark_index": "ctx-bench*"})
        assert get_es_benchmark_index() == "ctx-bench*"


# ---------------------------------------------------------------------------
# orion_metrics
# ---------------------------------------------------------------------------

class TestOrionMetrics:
    """orion_metrics() lists metric names per config."""

    def test_lists_metric_names(self, monkeypatch, make_result):
        """Metric names exclude the 'runs' and 'timestamp' bookkeeping keys."""
        runs = _orion_runs((1, {"podReadyLatency_P99": 5, "ovnCPU_avg": 2}))
        runner = mock.AsyncMock(return_value=make_result(stdout=runs))
        monkeypatch.setattr(orion_utils, "run_orion", runner)

        result = asyncio.run(orion_metrics(["/cfg/a.yaml"], version="4.22", input_vars={"x": 1}))

        assert result == {"/cfg/a.yaml": ["podReadyLatency_P99", "ovnCPU_avg"]}
        kwargs = runner.call_args.kwargs
        assert kwargs["config"] == "/cfg/a.yaml"
        assert kwargs["version"] == "4.22"
        assert kwargs["lookback"] == DEFAULT_LOOKBACK_DAYS
        assert kwargs["input_vars"] == {"x": 1}

    def test_no_data_gives_empty_list(self, monkeypatch, make_result):
        """A config with no data maps to an empty metric list."""
        runner = mock.AsyncMock(return_value=make_result(stdout="[]", returncode=3))
        monkeypatch.setattr(orion_utils, "run_orion", runner)
        assert asyncio.run(orion_metrics(["a.yaml"])) == {"a.yaml": []}

    def test_error_returns_string(self, monkeypatch, make_result):
        """A summarize error is surfaced as an error string."""
        runner = mock.AsyncMock(return_value=make_result(stdout="garbage"))
        monkeypatch.setattr(orion_utils, "run_orion", runner)
        result = asyncio.run(orion_metrics(["a.yaml"]))
        assert isinstance(result, str)
        assert result.startswith("Error processing result for a.yaml")


# ---------------------------------------------------------------------------
# compute_correlation
# ---------------------------------------------------------------------------

class TestComputeCorrelation:
    """compute_correlation() returns Pearson r or NaN for degenerate input."""

    def test_perfect_positive(self):
        """Linearly increasing series correlate at +1."""
        assert compute_correlation([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)

    def test_perfect_negative(self):
        """Inversely related series correlate at -1."""
        assert compute_correlation([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)

    def test_returns_python_float(self):
        """The result is a plain float, not a numpy scalar."""
        assert type(compute_correlation([1, 2, 3], [1, 3, 2])) is float  # pylint: disable=unidiomatic-typecheck

    @pytest.mark.parametrize(
        "values1, values2",
        [
            ([1, 2, 3], [1, 2]),       # unequal length
            ([1], [1]),                # fewer than two items
            ([], []),                  # empty
            ([5, 5, 5], [1, 2, 3]),    # zero variance in first
            ([1, 2, 3], [7, 7, 7]),    # zero variance in second
        ],
    )
    def test_degenerate_inputs_return_nan(self, values1, values2):
        """Degenerate inputs return NaN instead of raising."""
        assert math.isnan(compute_correlation(values1, values2))


# ---------------------------------------------------------------------------
# list_orion_configs
# ---------------------------------------------------------------------------

def _github_response(payload, status=200):
    return httpx.Response(status, json=payload, request=httpx.Request("GET", GITHUB_CONFIGS_URL))


class TestListOrionConfigs:
    """list_orion_configs() tries GitHub, then the local examples directory."""

    @pytest.fixture
    def local_dir(self, tmp_path, monkeypatch):
        """Point ORION_CONFIGS_PATH at a temp dir with a few files."""
        (tmp_path / "local-a.yaml").write_text("a: 1\n")
        (tmp_path / "local-b.yaml").write_text("b: 1\n")
        (tmp_path / "README.md").write_text("docs\n")
        monkeypatch.setattr(orion_utils, "ORION_CONFIGS_PATH", str(tmp_path))
        return tmp_path

    def test_github_listing_filters_yaml_files(self, monkeypatch):
        """Only YAML files (not dirs or other files) are returned from GitHub."""
        payload = [
            {"name": "cluster-density.yaml", "type": "file"},
            {"name": "metrics", "type": "dir"},
            {"name": "README.md", "type": "file"},
            {"name": "nested.yaml", "type": "dir"},
            {"name": "node-density.yaml", "type": "file"},
        ]
        getter = mock.Mock(return_value=_github_response(payload))
        monkeypatch.setattr(orion_utils.httpx, "get", getter)

        assert list_orion_configs() == ["cluster-density.yaml", "node-density.yaml"]
        assert getter.call_args.args[0] == GITHUB_CONFIGS_URL

    @pytest.mark.usefixtures("local_dir")
    def test_falls_back_to_local_on_http_error(self, monkeypatch):
        """An HTTP error from GitHub falls back to the local directory."""
        monkeypatch.setattr(orion_utils.httpx, "get", mock.Mock(return_value=_github_response({}, 403)))
        assert sorted(list_orion_configs()) == ["local-a.yaml", "local-b.yaml"]

    @pytest.mark.usefixtures("local_dir")
    def test_falls_back_to_local_on_network_error(self, monkeypatch):
        """A connection error from GitHub falls back to the local directory."""
        monkeypatch.setattr(orion_utils.httpx, "get", mock.Mock(side_effect=httpx.ConnectError("boom")))
        assert sorted(list_orion_configs()) == ["local-a.yaml", "local-b.yaml"]

    @pytest.mark.usefixtures("local_dir")
    def test_falls_back_to_local_when_github_empty(self, monkeypatch):
        """An empty GitHub listing falls back to the local directory."""
        monkeypatch.setattr(orion_utils.httpx, "get", mock.Mock(return_value=_github_response([])))
        assert sorted(list_orion_configs()) == ["local-a.yaml", "local-b.yaml"]

    def test_returns_empty_when_everything_fails(self, monkeypatch, tmp_path):
        """With GitHub down and no local directory, an empty list is returned."""
        monkeypatch.setattr(orion_utils.httpx, "get", mock.Mock(side_effect=httpx.ConnectError("boom")))
        monkeypatch.setattr(orion_utils, "ORION_CONFIGS_PATH", str(tmp_path / "missing"))
        assert not list_orion_configs()


# ---------------------------------------------------------------------------
# Plot generation
# ---------------------------------------------------------------------------

class TestPlots:
    """Plot helpers return base64-encoded PNG bytes."""

    def test_correlation_plot_is_base64_png(self):
        """The correlation plot decodes to a PNG image."""
        encoded = generate_correlation_plot([1, 2, 3], [2, 4, 7], "m1", "m2", title_prefix="cfg: ")
        assert isinstance(encoded, bytes)
        assert base64.b64decode(encoded).startswith(PNG_MAGIC)

    def test_correlation_plot_handles_nan_correlation(self):
        """A degenerate correlation (NaN) still renders a plot."""
        encoded = generate_correlation_plot([1, 1], [2, 3], "m1", "m2")
        assert base64.b64decode(encoded).startswith(PNG_MAGIC)

    def test_multi_line_plot_is_base64_png(self):
        """The multi-line plot decodes to a PNG image; empty series are skipped."""
        encoded = generate_multi_line_plot({"4.19": [1.0, 2.0], "4.20": [], "4.21": [3.0]}, "metric")
        assert base64.b64decode(encoded).startswith(PNG_MAGIC)

    def test_multi_line_plot_rejects_empty_input(self):
        """An empty series mapping raises ValueError."""
        with pytest.raises(ValueError, match="empty"):
            generate_multi_line_plot({}, "metric")


# ---------------------------------------------------------------------------
# parse_nightly_version
# ---------------------------------------------------------------------------

class TestParseNightlyVersion:
    """parse_nightly_version() handles nightly and GA strings."""

    def test_nightly(self):
        """A nightly string yields major version and build datetime."""
        info = parse_nightly_version("4.18.0-0.nightly-2025-01-15-203335")
        assert info == NightlyVersionInfo(
            full_version="4.18.0-0.nightly-2025-01-15-203335",
            major_version="4.18",
            nightly_date=datetime(2025, 1, 15, 20, 33, 35),
            is_nightly=True,
        )

    def test_nightly_with_surrounding_whitespace(self):
        """Leading/trailing whitespace is stripped before parsing."""
        info = parse_nightly_version("  5.0.0-0.nightly-2026-08-10-122052\n")
        assert info.full_version == "5.0.0-0.nightly-2026-08-10-122052"
        assert info.major_version == "5.0"
        assert info.is_nightly

    @pytest.mark.parametrize("version, major", [("4.17", "4.17"), ("4.17.3", "4.17"), ("10.2", "10.2")])
    def test_ga(self, version, major):
        """GA strings (X.Y or X.Y.Z) parse without a nightly date."""
        info = parse_nightly_version(version)
        assert info.major_version == major
        assert info.full_version == version
        assert info.nightly_date is None
        assert not info.is_nightly

    @pytest.mark.parametrize(
        "version",
        [
            "",
            "4",
            "latest",
            "4.18.0-0.ci-2025-01-15-203335",
            "4.18.0-0.nightly-2025-01-15",
            "4.18.0-0.nightly-2025-1-15-203335",
            "4.18.0-0.nightly-2025-01-15-2033",
            "4.18.0.1",
            "v4.18",
        ],
    )
    def test_invalid_format_raises(self, version):
        """Malformed strings raise ValueError with a helpful message."""
        with pytest.raises(ValueError, match="Invalid version format"):
            parse_nightly_version(version)

    def test_invalid_calendar_date_raises(self):
        """A well-formed string with an impossible date raises ValueError."""
        with pytest.raises(ValueError):
            parse_nightly_version("4.18.0-0.nightly-2025-13-45-203335")


# ---------------------------------------------------------------------------
# parse_timestamp
# ---------------------------------------------------------------------------

EPOCH_2023 = 1700000000
DT_2023 = datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


class TestParseTimestamp:
    """parse_timestamp() normalises timestamps to timezone-aware UTC datetimes."""

    @pytest.mark.parametrize("value", [EPOCH_2023, float(EPOCH_2023), str(EPOCH_2023), f"{EPOCH_2023}.0"])
    def test_unix_timestamps(self, value):
        """Unix timestamps as int, float, or numeric strings are supported."""
        assert parse_timestamp(value) == DT_2023

    def test_result_is_utc_aware(self):
        """Parsed values always carry UTC tzinfo."""
        assert parse_timestamp(EPOCH_2023).tzinfo == timezone.utc
        assert parse_timestamp("2023-11-14T22:13:20").tzinfo == timezone.utc

    @pytest.mark.parametrize(
        "value",
        [
            "2023-11-14T22:13:20",
            "2023-11-14T22:13:20Z",
            "2023-11-14T22:13:20.123456Z",
            "2023-11-14 22:13:20",
        ],
    )
    def test_iso_strings(self, value):
        """ISO-8601 strings (with/without Z and fractional seconds) parse as UTC."""
        assert parse_timestamp(value) == DT_2023

    @pytest.mark.parametrize("value", ["2023-11-14T22:13:20+05:00", "2023-11-14T22:13:20-05:00"])
    def test_explicit_offsets_are_discarded(self, value):
        """Current behaviour: an explicit UTC offset is dropped and the wall-clock time is treated as UTC."""
        assert parse_timestamp(value) == DT_2023

    def test_date_only_string(self):
        """A date-only ISO string parses to midnight UTC."""
        assert parse_timestamp("2023-11-14") == datetime(2023, 11, 14, tzinfo=timezone.utc)

    @pytest.mark.parametrize("value", [None, "", "not-a-date", "2023-13-45T00:00:00", [], {}, float("nan")])
    def test_unparseable_returns_none(self, value):
        """Unparseable values return None rather than raising."""
        assert parse_timestamp(value) is None


# ---------------------------------------------------------------------------
# filter_data_by_timestamp
# ---------------------------------------------------------------------------

class TestFilterDataByTimestamp:
    """filter_data_by_timestamp() keeps entries on or before the cutoff."""

    CUTOFF = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    def test_keeps_entries_on_or_before_cutoff(self):
        """Entries before and exactly at the cutoff are kept; later ones dropped."""
        before = {"id": "before", "timestamp": (self.CUTOFF - timedelta(days=1)).timestamp()}
        exact = {"id": "exact", "timestamp": self.CUTOFF.timestamp()}
        after = {"id": "after", "timestamp": (self.CUTOFF + timedelta(seconds=1)).timestamp()}
        result = filter_data_by_timestamp([before, exact, after], self.CUTOFF)
        assert [e["id"] for e in result] == ["before", "exact"]

    def test_mixed_timestamp_formats(self):
        """ISO strings and epoch numbers are compared on the same UTC basis."""
        data = [
            {"id": "iso-before", "timestamp": "2025-01-15T11:59:59Z"},
            {"id": "iso-after", "timestamp": "2025-01-15T12:00:01"},
            {"id": "epoch-str", "timestamp": str(int(self.CUTOFF.timestamp()))},
        ]
        result = filter_data_by_timestamp(data, self.CUTOFF)
        assert [e["id"] for e in result] == ["iso-before", "epoch-str"]

    def test_entries_without_valid_timestamp_are_dropped(self):
        """Entries with missing or unparseable timestamps are excluded."""
        data = [{"id": "missing"}, {"id": "bad", "timestamp": "nope"}, {"id": "none", "timestamp": None}]
        assert not filter_data_by_timestamp(data, self.CUTOFF)

    def test_empty_input(self):
        """An empty list yields an empty list."""
        assert not filter_data_by_timestamp([], self.CUTOFF)

    def test_cutoff_in_other_timezone(self):
        """An aware cutoff in a non-UTC zone is compared correctly."""
        est = timezone(timedelta(hours=-5))
        cutoff = datetime(2025, 1, 15, 7, 0, 0, tzinfo=est)  # == 12:00 UTC
        data = [{"id": "ok", "timestamp": "2025-01-15T12:00:00Z"}, {"id": "late", "timestamp": "2025-01-15T12:00:01Z"}]
        assert [e["id"] for e in filter_data_by_timestamp(data, cutoff)] == ["ok"]

    def test_naive_cutoff_raises(self):
        """The cutoff must be timezone-aware; a naive cutoff cannot be compared."""
        with pytest.raises(TypeError):
            filter_data_by_timestamp([{"timestamp": EPOCH_2023}], datetime(2025, 1, 1))
