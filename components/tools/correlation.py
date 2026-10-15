"""metrics_correlation tool."""

import logging
from typing import Annotated

from fastmcp import Context
from fastmcp.tools import tool
from mcp import types
from pydantic import Field

from utils.constants import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_METRIC,
    MISSING_CORRELATION_METRICS_MSG,
)
from utils.tool_helpers import (
    ConfigParam,
    InputVarsParam,
    LookbackParam,
    VersionParam,
    config_path,
    resolve_config_and_vars,
)
from utils.utils import generate_correlation_plot, run_orion, summarize_result

logger = logging.getLogger(__name__)


@tool()
async def metrics_correlation(
    metric1: Annotated[str, Field(description="First metric to analyze")] = DEFAULT_METRIC,
    metric2: Annotated[str, Field(description="Second metric to analyze")] = "ovnCPU_avg",
    *,
    config_name: ConfigParam = None,
    since: Annotated[str, Field(description="Date to begin looking back for performance data")] = None,
    version: VersionParam = "4.19",
    lookback: LookbackParam = DEFAULT_LOOKBACK_DAYS,
    input_vars: InputVarsParam = "",
    ctx: Context = None,
) -> types.ImageContent | types.TextContent:
    """Check if two metrics are correlated by computing Pearson coefficient and plotting a scatter chart. Use when a user asks "are these metrics related", "correlate X with Y", or "is ovnCPU correlated with podReadyLatency". Input: two metric names.

    Triggers: "correlate podReadyLatency with ovnCPU", "are ovnCPU and etcdCPU related",
    "is there a correlation between X and Y".

    Args:
        metric1: First metric, Y-axis (default: 'podReadyLatency_P99').
        metric2: Second metric, X-axis (default: 'ovnCPU_avg').
        config_name: Orion config filename (e.g. 'cluster-density.yaml').
        since: Start date for lookback (default: None).
        version: OpenShift version (default: '4.19').
        lookback: Days to look back (default: '15').
        input_vars: JSON string of template variables for the config.

    Returns:
        ImageContent (scatter-plot PNG) or TextContent (error).
    """
    config_value, iv = await resolve_config_and_vars(
        ctx, config_name, version, input_vars,
    )

    result = await run_orion(
        config=config_path(config_value),
        version=version,
        lookback=lookback,
        since=since,
        input_vars=iv,
    )

    summary = await summarize_result(result)

    # Ensure we received a valid dict back
    if not isinstance(summary, dict):
        logger.warning("Error processing Orion output: %s", summary)
        return types.TextContent(type="text", text=f"Error processing Orion output: {summary}")

    # Extract metric values
    try:
        values1 = summary[metric1]["value"]
        values2 = summary[metric2]["value"]
    except KeyError:
        return types.TextContent(
            type="text",
            text=MISSING_CORRELATION_METRICS_MSG,
        )

    # Compute correlation & generate plot
    corr_b64 = generate_correlation_plot(values1, values2, metric1, metric2, title_prefix=f"{config_value}: ")

    return types.ImageContent(type="image", data=corr_b64.decode("utf-8"), mimeType="image/png")
