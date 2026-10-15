"""Tests that server.py discovers every component via FileSystemProvider."""

import asyncio

from fastmcp import Client

import server

EXPECTED_TOOLS = {
    "discover_jobs",
    "get_orion_configs",
    "get_orion_metrics",
    "get_orion_metrics_with_meta",
    "get_orion_performance_data",
    "get_performance_summary",
    "get_release_date",
    "has_networking_regressed",
    "has_nightly_regressed",
    "has_openshift_regressed",
    "metrics_correlation",
    "openshift_report_on",
    "openshift_report_on_pr",
}

EXPECTED_RESOURCES = {"orion-mcp://release_dates", "orion-mcp://get_data_source"}


async def _list_components():
    async with Client(server.mcp) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        release_date = await client.call_tool("get_release_date", {"version": "4.20"})
    return tools, resources, release_date


def test_server_registers_all_components():
    """All tools and resources under components/ are exposed by the server."""
    tools, resources, release_date = asyncio.run(_list_components())

    assert {t.name for t in tools} == EXPECTED_TOOLS
    assert {str(r.uri) for r in resources} == EXPECTED_RESOURCES
    assert release_date.data == "2025-10-23"


def test_ctx_is_not_exposed_as_tool_parameter():
    """The injected FastMCP Context must not leak into tool input schemas."""
    tools, _, _ = asyncio.run(_list_components())

    for tool in tools:
        assert "ctx" not in tool.inputSchema.get("properties", {}), tool.name
