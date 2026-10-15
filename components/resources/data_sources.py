"""MCP resources exposing Orion data-source information."""

from fastmcp.resources import resource

from utils.constants import RELEASE_DATES
from utils.utils import get_data_source, redact_url


@resource("orion-mcp://release_dates")
def release_dates_resource() -> dict[str, str]:
    """
    Provides the release dates for the different OpenShift versions.
    """
    return RELEASE_DATES


@resource("orion-mcp://get_data_source")
def get_data_source_resource() -> str:
    """
    Provides the data source URL for Orion analysis.

    User must launch MCP server with the environment variable ES_SERVER
    set to the OpenSearch URL.

    Returns:
        The OpenSearch URL as a string, with any credentials (userinfo and
        sensitive query parameters) removed.
    """
    return redact_url(get_data_source())
