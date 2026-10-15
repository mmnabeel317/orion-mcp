"""get_performance_summary tool."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastmcp import Context
from fastmcp.tools import tool
from pydantic import Field

from utils.config_parser import load_config_metrics_with_meta
from utils.constants import DEFAULT_LOOKBACK_DAYS
from utils.tool_helpers import (
    ConfigParam,
    InputVarsParam,
    VersionParam,
    config_path,
    extract_and_set_es_server,
    parse_input_vars,
    resolve_config_and_vars,
    split_configs,
)
from utils.utils import parse_timestamp, run_orion, summarize_result

logger = logging.getLogger(__name__)


async def _summarize_single_config(
    config_value: str, version: str, lookback: int, iv: dict | None,
) -> dict:
    """Run Orion for one config and compute per-metric min/max/avg/change% stats."""
    full_path = config_path(config_value)

    try:
        metrics_list, meta_map = load_config_metrics_with_meta(full_path, version, input_vars=iv)
    except Exception as e:
        logger.error("Failed to load config metrics for %s: %s", config_value, e)
        return {"config": config_value, "success": False, "error": f"Failed to load config metrics: {e}"}

    try:
        result = await run_orion(config=full_path, version=version, lookback=str(lookback), input_vars=iv)
        sum_result = await summarize_result(result)
    except Exception as e:
        logger.error("Orion execution failed for %s: %s", config_value, e)
        return {"config": config_value, "success": False, "error": f"Orion failed: {e}"}

    if not isinstance(sum_result, dict):
        return {"config": config_value, "success": False, "error": f"Unexpected Orion output: {sum_result}"}

    # Prior window: double lookback, filter to runs strictly before current window cutoff
    cutoff_dt = datetime.now(tz=timezone.utc) - timedelta(days=lookback)
    prior_sum: dict[str, list] = {}
    try:
        prior_result = await run_orion(config=full_path, version=version, lookback=str(lookback * 2), input_vars=iv)
        prior_sum_raw = await summarize_result(prior_result)
        if isinstance(prior_sum_raw, dict):
            for run in prior_sum_raw.get("runs", []):
                run_dt = parse_timestamp(run.get("timestamp"))
                if run_dt is None or run_dt >= cutoff_dt:
                    continue
                for m_name, m_data in run.get("metrics", {}).items():
                    v = m_data.get("value")
                    if v is not None:
                        prior_sum.setdefault(m_name, []).append(v)
    except Exception as exc:
        logger.warning("Prior-period query failed for %s: %s", config_value, exc)

    metric_summaries = []
    for m_name in metrics_list:
        if m_name not in sum_result:
            continue
        values = [v for v in sum_result[m_name].get("value", []) if v is not None]
        if not values:
            continue
        avg_val = sum(values) / len(values)
        meta = meta_map.get(m_name, {})
        previous_values = prior_sum.get(m_name, [])
        change_pct = None
        if previous_values:
            prev_avg = sum(previous_values) / len(previous_values)
            if prev_avg != 0:
                change_pct = round(((avg_val - prev_avg) / abs(prev_avg)) * 100, 2)
        metric_summaries.append({
            "name": m_name,
            "runs": len(values),
            "min": round(min(values), 4),
            "max": round(max(values), 4),
            "avg": round(avg_val, 4),
            "change_percent": change_pct,
            "direction": meta.get("direction"),
            "threshold": meta.get("threshold"),
        })

    return {"config": config_value, "success": bool(metric_summaries), "metrics": metric_summaries}


# get_performance_summary replaces the N-per-metric call pattern: previously callers like BugZooka
# called a per-metric tool in a loop — O(metrics × configs × 2) ES round trips for a health check.
# This tool collapses that to O(configs) by aggregating all metrics server-side in one call.
@tool()
async def get_performance_summary(
    version: VersionParam = "4.19",
    lookback: Annotated[int, Field(description="Number of days to look back for data")] = int(DEFAULT_LOOKBACK_DAYS),
    config_name: ConfigParam = None,
    input_vars: InputVarsParam = "",
    ctx: Context = None,
) -> dict:
    """Health check — aggregated stats (min, max, avg, change%) across ALL metrics for one or more configs. Use when a user asks for an overall performance summary or health check for a version.

    Triggers: "how is 4.22 doing overall", "give me a performance summary for 5.0",
    "is 4.20 healthy", "overall performance report for 4.22".

    Args:
        version: OpenShift version (default: '4.19').
        lookback: Days to look back (default: 14).
        config_name: Orion config filename or comma-separated list (e.g. 'cluster-density.yaml,node-density.yaml').
        input_vars: JSON string of template variables for the config.

    Returns:
        Dict with per-config results, each containing per-metric stats (min, max, avg, change_percent, direction, threshold).
    """
    extract_and_set_es_server(ctx)
    try:
        iv = parse_input_vars(input_vars)
    except ValueError as exc:
        return {"success": False, "error": str(exc), "results": []}

    configs = split_configs(config_name)
    if not configs:
        config_value, iv = await resolve_config_and_vars(ctx, None, version, input_vars)
        configs = [config_value]

    results = await asyncio.gather(*[
        _summarize_single_config(c, version, lookback, iv) for c in configs
    ])

    return {"success": any(r.get("success") for r in results), "results": list(results)}
