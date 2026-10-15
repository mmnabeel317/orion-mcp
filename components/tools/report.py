"""openshift_report_on tool."""

import json
import logging
from typing import Annotated

from fastmcp import Context
from fastmcp.tools import tool
from mcp import types
from pydantic import Field

from utils.constants import DEFAULT_LOOKBACK_DAYS, DEFAULT_METRIC
from utils.tool_helpers import (
    ConfigParam,
    InputVarsParam,
    LookbackParam,
    config_path,
    extract_and_set_es_server,
    resolve_config_and_vars,
    split_configs,
)
from utils.utils import generate_multi_line_plot, run_orion, summarize_result

logger = logging.getLogger(__name__)


@tool()
async def openshift_report_on(
    versions: Annotated[str, Field(description="Comma-separated list of OpenShift versions e.g. '4.19,4.20'")] = "4.19",
    lookback: LookbackParam = DEFAULT_LOOKBACK_DAYS,
    since: Annotated[str, Field(description="Date to begin lookback")] = None,
    *,
    metric: Annotated[str, Field(description="Metric to analyze")] = DEFAULT_METRIC,
    config_name: ConfigParam = None,
    input_vars: InputVarsParam = "",
    options: Annotated[str, Field(description="Options in format 'output_format' or 'output_format:display_field'. Examples: 'image', 'json', 'both', 'json:ocpVirtVersion'")] = "image",
    ctx: Context = None,
) -> types.ImageContent | types.TextContent:
    """Show or compare a specific metric across OpenShift versions. Use when a user asks to visualize, plot, or compare metric values across versions.

    Triggers: "show podReadyLatency for 4.22", "compare ovnCPU for 4.22 vs 5.0", "plot etcdCPU over time".

    Args:
        versions: Comma-separated versions to analyze (default: '4.19'). E.g. '4.22,5.0'.
        lookback: Days to look back (default: '15').
        since: Start date for lookback (default: None).
        metric: Metric to plot (default: 'podReadyLatency_P99').
        config_name: Orion config filename or comma-separated list (e.g. 'cluster-density.yaml,node-density.yaml').
                     When multiple configs are provided, each appears as a separate line on the chart.
        input_vars: JSON string of template variables for the config.
        options: Output format — 'image' (default), 'json', 'both', or 'json:displayField'.

    Returns:
        Image (PNG chart) by default, or JSON data keyed by config and version when options='json'.
        Single config returns {config, metric, data}; multiple configs return {results: [...]}.
    """
    extract_and_set_es_server(ctx)

    if ":" in options:
        output_format, display = options.split(":", 1)
    else:
        output_format = options
        display = ""

    if isinstance(versions, str):
        version_list = [v.strip() for v in versions.split(',') if v.strip()]
    else:
        version_list = list(versions)

    first_ver = version_list[0] if version_list else "4.19"
    configs = split_configs(config_name)
    if not configs:
        config_value, iv = await resolve_config_and_vars(ctx, None, first_ver, input_vars)
        configs = [config_value]
    else:
        _, iv = await resolve_config_and_vars(ctx, None, first_ver, input_vars)

    all_series: dict[str, list[float]] = {}
    all_full_data: list[dict] = []
    all_errors = []

    for cfg in configs:
        series: dict[str, list[float]] = {}
        full_data: dict[str, dict] = {}
        errors = []

        for ver in version_list:
            result = await run_orion(
                config=config_path(cfg),
                version=ver,
                lookback=lookback,
                since=since,
                input_vars=iv,
                display=display if display.strip() else None,
            )

            sum_result = await summarize_result(result, isolate=metric)

            if not isinstance(sum_result, dict) or metric not in sum_result:
                logger.warning("No data for metric %s, version %s, config %s", metric, ver, cfg)
                errors.append(f"[{cfg}] No data for version {ver}, metric {metric}")
                continue

            raw_values = sum_result[metric].get("value", [])
            if not isinstance(raw_values, list):
                errors.append(f"[{cfg}] Unexpected data format for version {ver}")
                continue

            values = [v for v in raw_values if v is not None]
            if not values:
                errors.append(f"[{cfg}] All values are None for version {ver}")
                continue

            label = f"{cfg}:{ver}" if len(configs) > 1 else ver
            series[label] = values
            full_data[ver] = sum_result
            logger.debug("series: %s", series)


        all_errors.extend(errors)
        if series:
            all_series.update(series)
            all_full_data.append({"config": cfg, "metric": metric, "lookback": lookback,
                                   "display": display if display.strip() else None, "data": full_data})

    if all_errors and not all_series:
        return types.TextContent(type="text", text="\n".join(all_errors))

    if output_format.lower() == "json":
        output = all_full_data[0] if len(all_full_data) == 1 else {"results": all_full_data}
        return types.TextContent(type="text", text=json.dumps(output, indent=2))

    if output_format.lower() == "both":
        output = all_full_data[0] if len(all_full_data) == 1 else {"results": all_full_data}
        output["plot_info"] = "Image data follows JSON data"
        try:
            img_b64 = generate_multi_line_plot(all_series, metric)
            combined = json.dumps(output, indent=2) + "\n\n[IMAGE_DATA_BASE64]\n" + img_b64.decode("utf-8")
            return types.TextContent(type="text", text=combined)
        except ValueError as e:
            return types.TextContent(type="text", text=f"Error generating plot: {e}\n\nJSON data:\n{json.dumps(output, indent=2)}")

    try:
        img_b64 = generate_multi_line_plot(all_series, metric)
        return types.ImageContent(type="image", data=img_b64.decode("utf-8"), mimeType="image/png")
    except ValueError as e:
        return types.TextContent(type="text", text=str(e))
