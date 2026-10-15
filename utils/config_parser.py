"""
Orion config parsing helpers for orion-mcp.

Renders Orion's Jinja2-templated YAML configs and extracts the metric names
and per-metric metadata (direction, threshold, ...) they define.
"""

import json
import os

import jinja2
import yaml


def metric_key(metric: dict) -> str:
    """Return the metric name Orion reports for a metric definition.

    Orion suffixes the metric name with the aggregation type when one is
    configured, otherwise with the ``metric_of_interest`` field.
    """
    name = metric.get("name", "unknown")
    if "agg" in metric and isinstance(metric["agg"], dict):
        agg_type = metric["agg"].get("agg_type", "")
        if agg_type:
            return f"{name}_{agg_type}"
    metric_of_interest = metric.get("metric_of_interest", "value")
    return f"{name}_{metric_of_interest}"


def render_config_yaml(config_path: str, version: str = "", input_vars: dict | str | None = None) -> dict:
    """Render an Orion config template and parse the resulting YAML.

    Template variables come from the (lower-cased) environment, a set of
    defaults, and ``input_vars`` — in increasing order of precedence.
    """
    with open(config_path, "r", encoding="utf-8") as template_file:
        template_content = template_file.read()

    env_vars = {k.lower(): v for k, v in os.environ.items()}
    defaults = {
        "version": version,
        "jobtype": "periodic",
        "pull_number": 0,
        "organization": "",
        "repository": "",
    }
    for k, v in defaults.items():
        if v or k not in env_vars:
            env_vars[k] = v

    if input_vars:
        iv = input_vars
        if isinstance(iv, str):
            iv = json.loads(iv)
        env_vars.update({str(k): str(v) for k, v in iv.items()})

    try:
        template = jinja2.Template(template_content, undefined=jinja2.StrictUndefined)
        rendered = template.render(env_vars)
    except jinja2.exceptions.UndefinedError:
        template = jinja2.Template(template_content)
        rendered = template.render(env_vars)

    return yaml.safe_load(rendered)


def load_config_metrics_with_meta(config_path: str, version: str = "", input_vars: dict | str | None = None) -> tuple[list[str], dict]:
    """Return the metric names defined by a config and per-metric metadata.

    Metrics are collected from inline ``tests[].metrics`` entries and from any
    referenced ``metricsFile`` (top-level or per-test). Metadata-type metrics
    are skipped.
    """
    rendered_config = render_config_yaml(config_path, version, input_vars=input_vars)
    metrics_list: list[str] = []
    meta_map: dict = {}

    def _process_metric(metric: dict) -> None:
        if metric.get("type") == "metadata":
            return
        key = metric_key(metric)
        metrics_list.append(key)
        direction_raw = metric.get("direction")
        threshold_raw = metric.get("threshold")
        try:
            direction_val = (
                int(direction_raw) if direction_raw is not None else None
            )
        except (TypeError, ValueError):
            direction_val = None
        try:
            threshold_val = (
                float(threshold_raw) if threshold_raw is not None else None
            )
        except (TypeError, ValueError):
            threshold_val = None
        meta_map[key] = {
            "direction": direction_val,
            "threshold": threshold_val,
            "metric_of_interest": metric.get("metric_of_interest"),
            "agg_type": metric.get("agg", {}).get("agg_type") if isinstance(metric.get("agg"), dict) else None,
        }

    def _load_metrics_file(mf_name):
        mf_path = os.path.join(os.path.dirname(config_path), mf_name)
        try:
            mf_config = render_config_yaml(mf_path, version, input_vars=input_vars)
            mf_metrics = mf_config if isinstance(mf_config, list) else mf_config.get("metrics", [])
            for metric in mf_metrics:
                if isinstance(metric, dict):
                    _process_metric(metric)
        except (OSError, KeyError, ValueError, TypeError):
            pass

    top_metrics_file = rendered_config.get("metricsFile")
    if top_metrics_file:
        _load_metrics_file(top_metrics_file)

    for test in rendered_config.get("tests", []):
        test_metrics_file = test.get("metricsFile")
        if test_metrics_file:
            _load_metrics_file(test_metrics_file)

        for metric in test.get("metrics", []):
            _process_metric(metric)

    return metrics_list, meta_map
