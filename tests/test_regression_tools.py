"""Tests for components.tools.regression."""

import asyncio
import os
from datetime import datetime, timezone

import pytest

from components.tools import regression
from components.tools.regression import (
    _extract_regression_details,
    _timestamp_after,
    has_networking_regressed,
    has_nightly_regressed,
    has_openshift_regressed,
)
from tests.helpers import make_result
from utils.constants import (
    DEFAULT_NETWORKING_CONFIGS,
    NO_CHANGEPOINTS_MSG,
    NO_REGRESSIONS_MSG,
    ORION_CONFIGS_PATH,
)

CHANGEPOINT_RUNS = [
    {
        "timestamp": "2026-01-01T00:00:00",
        "ocpVersion": "4.22.0-0.nightly-2026-01-01-000000",
        "prs": ["pr-1"],
        "is_changepoint": False,
        "metrics": {"podReadyLatency_P99": {"value": 1.0, "percentage_change": 0}},
    },
    {
        "timestamp": "2026-01-05T00:00:00",
        "ocpVersion": "4.22.0-0.nightly-2026-01-05-000000",
        "buildUrl": "https://prow.example/build/2",
        "prs": ["pr-1", "pr-2"],
        "is_changepoint": True,
        "metrics": {
            "podReadyLatency_P99": {"value": 2.0, "percentage_change": 25.5},
            "ovnCPU_avg": {"value": 1.0, "percentage_change": -10},
            "etcdCPU_avg": {"value": 1.0, "percentage_change": 0},
        },
    },
]


def test_extract_regression_details():
    """Changepoints report prev version, newly added PRs and non-zero metric changes."""
    details = _extract_regression_details(make_result(CHANGEPOINT_RUNS).stdout)

    assert details == [{
        "buildUrl": "https://prow.example/build/2",
        "ocpVersion": "4.22.0-0.nightly-2026-01-05-000000",
        "previousOcpVersion": "4.22.0-0.nightly-2026-01-01-000000",
        "prs_added": ["pr-2"],
        "metrics": ["podReadyLatency_P99 increased by 25.50%", "ovnCPU_avg decreased by 10.00%"],
    }]


def test_extract_regression_details_non_list():
    """Non-list Orion output yields no details."""
    assert not _extract_regression_details('{"periodic_avg": {}}')


def test_timestamp_after():
    """Strictly-after comparison; unparseable timestamps are never after."""
    cutoff = datetime(2026, 1, 3, tzinfo=timezone.utc)
    assert _timestamp_after("2026-01-04T00:00:00", cutoff)
    assert not _timestamp_after("2026-01-03T00:00:00", cutoff)
    assert not _timestamp_after("bad", cutoff)


def test_has_openshift_regressed_formats_changepoints(fake_orion):
    """Detected changepoints are formatted per config with PRs and metrics."""
    fake = fake_orion(regression, make_result(CHANGEPOINT_RUNS))

    out = asyncio.run(has_openshift_regressed(version="4.22", config_name="cluster-density.yaml"))

    assert "⚠️ Change detected in configuration: 'cluster-density.yaml'" in out
    assert "Previous OCP Version: 4.22.0-0.nightly-2026-01-01-000000" in out
    assert "  - pr-2" in out
    assert "  - podReadyLatency_P99 increased by 25.50%" in out
    assert fake.calls[0]["config"] == os.path.join(ORION_CONFIGS_PATH, "cluster-density.yaml")
    assert fake.calls[0]["version"] == "4.22"
    assert fake.calls[0]["jira_ack"] is True


def test_has_openshift_regressed_no_changepoints(fake_orion):
    """Exit code 3 (no data) and runs without changepoints both report no changepoints."""
    fake_orion(regression, make_result("", returncode=3))
    assert asyncio.run(has_openshift_regressed(version="4.22")) == NO_CHANGEPOINTS_MSG

    fake_orion(regression, make_result(CHANGEPOINT_RUNS[:1]))
    assert asyncio.run(has_openshift_regressed(version="4.22")) == NO_CHANGEPOINTS_MSG


def test_has_openshift_regressed_reports_orion_failure(fake_orion):
    """Unparseable Orion output is surfaced as an error line for that config."""
    fake_orion(regression, make_result("Traceback...", returncode=1, stderr="boom"))

    out = asyncio.run(has_openshift_regressed(version="4.22", config_name="a.yaml"))

    assert out == "❌ Error: Orion failed for a.yaml (exit 1): boom"


def test_has_openshift_regressed_bad_input_vars(fake_orion):
    """Malformed input_vars short-circuits before Orion runs."""
    fake = fake_orion(regression, make_result([]))

    out = asyncio.run(has_openshift_regressed(version="4.22", input_vars="{oops"))

    assert out.startswith("Error: Malformed input_vars JSON")
    assert not fake.calls


def test_has_networking_regressed_uses_networking_defaults(fake_orion):
    """Without config_name the networking tool runs the default networking configs."""
    fake = fake_orion(regression, make_result([]))

    asyncio.run(has_networking_regressed(version="4.22"))

    assert [c["config"] for c in fake.calls] == [
        os.path.join(ORION_CONFIGS_PATH, c) for c in DEFAULT_NETWORKING_CONFIGS
    ]


def test_has_nightly_regressed_validation_errors(fake_orion):
    """Invalid, GA, or out-of-order nightly inputs are rejected without running Orion."""
    fake = fake_orion(regression, make_result([]))

    assert asyncio.run(has_nightly_regressed("nope")).startswith("Error parsing nightly version")
    assert asyncio.run(has_nightly_regressed("4.22")) == "Error: '4.22' is not a nightly version."
    assert asyncio.run(has_nightly_regressed(
        "4.22.0-0.nightly-2026-01-01-000000",
        previous_nightly="4.22.0-0.nightly-2026-01-05-000000",
    )) == "Error: previous_nightly must be earlier than nightly_version."
    assert not fake.calls


# Pre-existing bug: parse_nightly_version() returns a naive datetime while
# parse_timestamp() returns a UTC-aware one, so filter_data_by_timestamp()
# raises TypeError as soon as Orion returns timestamped runs. strict=True makes
# these tests fail once the bug is fixed, so the markers get removed.
NIGHTLY_TZ_BUG = pytest.mark.xfail(
    raises=TypeError,
    strict=True,
    reason="naive nightly_date compared with tz-aware run timestamps",
)


@NIGHTLY_TZ_BUG
def test_has_nightly_regressed_scopes_to_nightly_window(fake_orion):
    """Runs after the nightly are excluded; changepoints within the window are reported."""
    later_run = dict(CHANGEPOINT_RUNS[1], timestamp="2026-02-01T00:00:00")
    fake = fake_orion(regression, make_result(CHANGEPOINT_RUNS + [later_run]))

    out = asyncio.run(has_nightly_regressed("4.22.0-0.nightly-2026-01-06-000000", config_name="a.yaml"))

    assert out.count("⚠️ Regression in 4.22.0-0.nightly-2026-01-06-000000") == 1
    assert "PRs: pr-2" in out
    assert fake.calls[0]["version"] == "4.22"


@NIGHTLY_TZ_BUG
def test_has_nightly_regressed_no_regressions(fake_orion):
    """A nightly older than every changepoint reports no regressions."""
    fake_orion(regression, make_result(CHANGEPOINT_RUNS))

    out = asyncio.run(has_nightly_regressed("4.22.0-0.nightly-2026-01-02-000000"))

    assert out == NO_REGRESSIONS_MSG
