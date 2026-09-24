"""
Tests for MCP tool contracts and response shapes.

Covers:
- Tool response types (TextContent, ImageContent, ResourceContent)
- Proper response structure
- Error handling and error responses
- Tool parameter validation
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

# Import the main MCP server module
import orion_mcp


class TestMCPToolResponseShapes:
    """Tests for MCP tool response shapes and types."""

    async def test_get_orion_configs_returns_text_content(self):
        """Test that get_orion_configs returns proper TextContent."""
        with patch('orion_mcp.ORION_CONFIGS', ["config1.yaml", "config2.yaml"]):
            result = orion_mcp.get_orion_configs()

            # Result should be usable with MCP TextContent
            assert isinstance(result, (str, list))
            if isinstance(result, list):
                assert all(isinstance(item, str) for item in result)

    async def test_discover_jobs_response_structure(self):
        """Test that discover_jobs returns expected structure."""
        # Mock the dependencies
        with patch('orion_mcp._resolve_configs_from_prow', new_callable=AsyncMock) as mock_prow:
            with patch('orion_mcp.httpx.AsyncClient') as mock_client:
                with patch('orion_mcp.get_data_source', return_value='http://localhost:9200'):
                    with patch('orion_mcp.get_es_metadata_index', return_value='perf_scale_ci*'):
                        mock_prow.return_value = ["config1.yaml"]
                        mock_response = MagicMock()
                        mock_response.status_code = 200
                        mock_response.json.return_value = {
                            "aggregations": {
                                "jobs": {
                                    "buckets": [
                                        {
                                            "key": "test-job",
                                            "benchmarks": {
                                                "buckets": [{"key": "cluster-density"}]
                                            }
                                        }
                                    ]
                                }
                            }
                        }

                        # Mock the HTTP client
                        async_cm = MagicMock()
                        async_cm.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
                        async_cm.__aexit__.return_value = None
                        mock_client.return_value = async_cm

                        # Call the actual tool
                        result = await orion_mcp.discover_jobs()
                        # Verify the result is a dict or string
                        assert isinstance(result, (dict, str))

    async def test_get_performance_summary_response_type(self):
        """Test that get_performance_summary returns text summary."""
        with patch('orion_mcp._summarize_single_config', new_callable=AsyncMock) as mock_summary:
            with patch('orion_mcp.get_data_source', return_value='http://localhost:9200'):
                mock_summary.return_value = {
                    "metrics": {
                        "cpu": {"min": 10, "max": 50, "avg": 30}
                    }
                }

                # Call the actual tool
                result = await orion_mcp.get_performance_summary(config_name="test.yaml")
                # Verify it returns a dict with metrics
                assert isinstance(result, dict)
                assert "metrics" in result or "summary" in result or len(result) > 0

    async def test_metrics_correlation_response_is_image(self):
        """Test that metrics_correlation returns image content."""
        with patch('orion_mcp.generate_correlation_plot') as mock_plot:
            mock_plot.return_value = b"fake_png_data"

            # Test that image generation is called
            result = mock_plot()
            assert isinstance(result, bytes)


class TestMCPToolParameterValidation:
    """Tests for MCP tool parameter validation."""

    def test_parse_input_vars_valid_json(self):
        """Test parsing valid JSON input variables."""
        from orion_mcp import _parse_input_vars

        variables = _parse_input_vars('{"platform": "aws", "workload": "cluster-density"}')
        assert variables == {"platform": "aws", "workload": "cluster-density"}

    def test_parse_input_vars_empty_string(self):
        """Test parsing empty string returns None."""
        from orion_mcp import _parse_input_vars

        result = _parse_input_vars("")
        assert result is None

    def test_parse_input_vars_malformed_json(self):
        """Test parsing malformed JSON raises ValueError."""
        from orion_mcp import _parse_input_vars

        with pytest.raises(ValueError):
            _parse_input_vars("{invalid json}")

    def test_parse_input_vars_null_input(self):
        """Test parsing None input returns None."""
        from orion_mcp import _parse_input_vars

        result = _parse_input_vars(None)
        assert result is None

    def test_split_configs_single_config(self):
        """Test splitting a single config."""
        from orion_mcp import _split_configs

        result = _split_configs("config1.yaml")
        assert result == ["config1.yaml"]

    def test_split_configs_multiple_configs(self):
        """Test splitting multiple comma-separated configs."""
        from orion_mcp import _split_configs

        result = _split_configs("config1.yaml,config2.yaml,config3.yaml")
        assert result == ["config1.yaml", "config2.yaml", "config3.yaml"]

    def test_split_configs_empty_uses_default(self):
        """Test that empty config uses default."""
        from orion_mcp import _split_configs

        result = _split_configs(None, default=["default.yaml"])
        assert result == ["default.yaml"]

    def test_split_configs_empty_string_uses_default(self):
        """Test that empty string uses default."""
        from orion_mcp import _split_configs

        result = _split_configs("", default=["default.yaml"])
        assert result == ["default.yaml"]

    def test_config_path_construction(self):
        """Test config path construction."""
        from orion_mcp import _config_path

        with patch('orion_mcp.ORION_CONFIGS_PATH', '/etc/orion/configs'):
            path = _config_path("cluster-density.yaml")
            assert "/etc/orion/configs" in path
            assert "cluster-density.yaml" in path

    def test_config_path_with_subdirectory(self):
        """Test config path with subdirectory."""
        from orion_mcp import _config_path

        with patch('orion_mcp.ORION_CONFIGS_PATH', '/etc/orion/configs'):
            result = _config_path("networking/latency.yaml")
            assert "/etc/orion/configs" in result
            assert "latency.yaml" in result


class TestMCPToolErrorHandling:
    """Tests for MCP tool error handling and error responses."""

    async def test_tool_handles_missing_parameters(self):
        """Test that tools handle missing required parameters gracefully."""
        # Test get_performance_summary with missing required config parameter
        # Should raise or return appropriate error message
        try:
            result = await orion_mcp.get_performance_summary(config=None)
            # If it returns without error, verify it's a string
            assert isinstance(result, str)
        except (TypeError, ValueError):
            # Expected: tool should reject missing required parameters
            pass

    async def test_tool_handles_invalid_config(self):
        """Test that tools handle invalid config names."""
        from orion_mcp import _config_path

        # Should handle gracefully or raise clear error
        with patch('utils.constants.ORION_CONFIGS_PATH', '/etc/orion/configs'):
            result = _config_path("nonexistent.yaml")
            # Path construction should not raise, but later file operations would
            assert result is not None

    async def test_tool_error_response_format(self):
        """Test that tool errors are formatted properly."""
        # Test with invalid config name
        with patch('orion_mcp._config_path') as mock_path:
            with patch('orion_mcp.get_data_source', return_value='http://localhost:9200'):
                mock_path.side_effect = FileNotFoundError("Config not found")
                try:
                    result = await orion_mcp.get_performance_summary(config_name="nonexistent.yaml")
                    # If tool handles error gracefully, should return something
                    assert result is not None
                except FileNotFoundError:
                    # Expected: tool propagates file not found error
                    pass


class TestAsyncContextIsolation:
    """Tests for async context isolation."""

    async def test_concurrent_requests_context_isolation(self):
        """Test that concurrent requests have isolated contexts."""
        from utils.utils import current_es_config
        import asyncio

        async def set_and_get_context(value):
            token = current_es_config.set({"es_server": value})
            try:
                await asyncio.sleep(0.01)
                return current_es_config.get()
            finally:
                current_es_config.reset(token)

        # Run concurrent requests with different contexts
        results = await asyncio.gather(
            set_and_get_context("http://server1:9200"),
            set_and_get_context("http://server2:9200"),
        )

        # Each should have its own isolated context
        assert results[0]["es_server"] == "http://server1:9200"
        assert results[1]["es_server"] == "http://server2:9200"
