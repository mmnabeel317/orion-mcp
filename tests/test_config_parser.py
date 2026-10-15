"""Tests for utils.config_parser."""

import textwrap

from utils.config_parser import load_config_metrics_with_meta, metric_key, render_config_yaml


def test_metric_key_uses_agg_type():
    """Aggregated metrics are suffixed with the aggregation type."""
    assert metric_key({"name": "ovnCPU", "agg": {"agg_type": "avg"}}) == "ovnCPU_avg"


def test_metric_key_uses_metric_of_interest():
    """Non-aggregated metrics are suffixed with metric_of_interest (default 'value')."""
    assert metric_key({"name": "podReadyLatency", "metric_of_interest": "P99"}) == "podReadyLatency_P99"
    assert metric_key({"name": "foo"}) == "foo_value"
    assert metric_key({}) == "unknown_value"


def test_render_config_yaml_applies_version_and_input_vars(tmp_path, monkeypatch):
    """Template variables come from defaults, env and input_vars (highest precedence)."""
    monkeypatch.setenv("PLATFORM", "GCP")
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(textwrap.dedent("""\
        version: "{{ version }}"
        platform: "{{ platform }}"
        jobtype: "{{ jobtype }}"
        """), encoding="utf-8")

    assert render_config_yaml(str(cfg), "4.20") == {"version": "4.20", "platform": "GCP", "jobtype": "periodic"}
    assert render_config_yaml(str(cfg), "4.20", input_vars={"platform": "AWS"})["platform"] == "AWS"
    assert render_config_yaml(str(cfg), "4.20", input_vars='{"platform": "Azure"}')["platform"] == "Azure"


def test_render_config_yaml_tolerates_undefined_variables(tmp_path):
    """Undefined variables render as empty instead of failing."""
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text('value: "x{{ not_defined_anywhere_xyz }}y"\n', encoding="utf-8")

    assert render_config_yaml(str(cfg)) == {"value": "xy"}


def test_load_config_metrics_with_meta(tmp_path):
    """Metrics come from inline tests and referenced metricsFile; metadata entries are skipped."""
    (tmp_path / "metrics.yaml").write_text(textwrap.dedent("""\
        - name: ovnCPU
          agg:
            agg_type: avg
          direction: -1
          threshold: 10
        - name: clusterInfo
          type: metadata
        """), encoding="utf-8")
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(textwrap.dedent("""\
        tests:
          - name: test1
            metricsFile: metrics.yaml
            metrics:
              - name: podReadyLatency
                metric_of_interest: P99
                direction: "1"
                threshold: not-a-number
        """), encoding="utf-8")

    metrics, meta = load_config_metrics_with_meta(str(cfg), "4.20")

    assert metrics == ["ovnCPU_avg", "podReadyLatency_P99"]
    assert meta["ovnCPU_avg"] == {
        "direction": -1,
        "threshold": 10.0,
        "metric_of_interest": None,
        "agg_type": "avg",
    }
    assert meta["podReadyLatency_P99"]["direction"] == 1
    assert meta["podReadyLatency_P99"]["threshold"] is None


def test_load_config_metrics_with_meta_ignores_missing_metrics_file(tmp_path):
    """A metricsFile that cannot be read is skipped rather than failing the whole config."""
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(textwrap.dedent("""\
        metricsFile: does-not-exist.yaml
        tests:
          - name: test1
            metrics:
              - name: etcdCPU
                agg:
                  agg_type: max
        """), encoding="utf-8")

    metrics, _ = load_config_metrics_with_meta(str(cfg))

    assert metrics == ["etcdCPU_max"]
