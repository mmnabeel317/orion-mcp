"""get_orion_metrics and get_orion_metrics_with_meta tools."""

from fastmcp import Context
from fastmcp.tools import tool

from utils.config_parser import load_config_metrics_with_meta
from utils.tool_helpers import (
    ConfigParam,
    InputVarsParam,
    VersionParam,
    config_path,
    resolve_config_and_vars,
)
from utils.utils import orion_metrics


@tool()
async def get_orion_metrics(
    config_name: ConfigParam = None,
    version: VersionParam = "4.20",
    input_vars: InputVarsParam = "",
    ctx: Context = None,
) -> dict:
    """List what metrics a benchmark tracks. Use when a user asks "what metrics does cluster-density have" or "list metrics for node-density".

    Triggers: "what metrics does cluster-density track", "list metrics for node-density",
    "what can I measure for this config".

    Args:
        config_name: Orion config filename (default: 'cluster-density.yaml').
        version: OpenShift version (default: '4.20').
        input_vars: JSON string of template variables for the config.

    Returns:
        Dict keyed by config with list of metric names.
    """
    effective_config, iv = await resolve_config_and_vars(ctx, config_name, version, input_vars)

    result = await orion_metrics([config_path(effective_config)], version=version, input_vars=iv)

    if isinstance(result, str):
        return {"error": f"Failed to fetch Orion metrics: {result}"}

    return result


@tool()
async def get_orion_metrics_with_meta(
    config_name: ConfigParam = None,
    version: VersionParam = "4.19",
    input_vars: InputVarsParam = "",
    ctx: Context = None,
) -> dict:
    """Get metric details including thresholds, directions (higher-is-better or lower-is-better), and labels for a benchmark. Use when a user asks "what are the thresholds", "which direction is good for ovnCPU", or "show metric metadata".

    Triggers: "what are the metric thresholds for cluster-density", "which metrics are higher-is-better",
    "show metric details".

    Args:
        config_name: Orion config filename (default: 'cluster-density.yaml').
        version: OpenShift version (default: '4.19').
        input_vars: JSON string of template variables for the config.

    Returns:
        Dict with "metrics" (list of names) and "meta" (per-metric label, direction, threshold).
    """
    effective_config, iv = await resolve_config_and_vars(ctx, config_name, version, input_vars)
    try:
        metrics, meta_map = load_config_metrics_with_meta(
            config_path(effective_config),
            version=version,
            input_vars=iv,
        )
        return {"metrics": metrics, "meta": meta_map}
    except Exception as e:
        result = await orion_metrics(
            [config_path(effective_config)], version=version, input_vars=iv,
        )
        if isinstance(result, str):
            return {"error": f"{e} | {result}"}
        return {"metrics": result, "meta": {}, "warning": str(e)}
