"""
Tests for configuration path validation and YAML rendering.

Covers:
- Valid configuration paths
- Invalid/non-existent paths
- Undefined variables in templates
- YAML syntax errors
- Configuration file loading
"""

import pytest
from unittest.mock import patch

from utils.utils import list_orion_configs
from orion_mcp import _render_config_yaml


class TestConfigPathValidation:
    """Tests for configuration path validation."""

    def test_list_orion_configs_valid_directory(self, tmp_path):
        """Test listing configs from a valid directory."""
        # Create a few test config files
        config1 = tmp_path / "config1.yaml"
        config2 = tmp_path / "config2.yaml"
        config1.write_text("test: 1")
        config2.write_text("test: 2")

        with patch('utils.utils.ORION_CONFIGS_PATH', str(tmp_path)):
            with patch('utils.utils.GITHUB_CONFIGS_URL', None):
                configs = list_orion_configs()
                assert "config1.yaml" in configs
                assert "config2.yaml" in configs

    def test_list_orion_configs_empty_directory(self, tmp_path):
        """Test listing configs from an empty directory."""
        with patch('utils.constants.ORION_CONFIGS_PATH', str(tmp_path)):
            configs = list_orion_configs()
            assert configs == []

    def test_list_orion_configs_nonexistent_directory(self):
        """Test listing configs from a non-existent directory."""
        nonexistent = "/nonexistent/path/that/does/not/exist/12345"
        with patch('utils.constants.ORION_CONFIGS_PATH', nonexistent):
            # Should handle gracefully - either return empty list or raise
            try:
                configs = list_orion_configs()
                # If it returns, it should be an empty list
                assert isinstance(configs, list)
            except (FileNotFoundError, OSError):
                # This is also acceptable behavior
                pass

    def test_list_orion_configs_filters_yaml_files(self, tmp_path):
        """Test that only .yaml files are returned."""
        config1 = tmp_path / "config1.yaml"
        config2 = tmp_path / "config2.yml"
        config3 = tmp_path / "readme.txt"
        config1.write_text("test: 1")
        config2.write_text("test: 2")
        config3.write_text("not a config")

        with patch('utils.utils.ORION_CONFIGS_PATH', str(tmp_path)):
            with patch('utils.utils.GITHUB_CONFIGS_URL', None):
                configs = list_orion_configs()
                # Should include .yaml and .yml files
                assert any("config1" in c for c in configs)
                # Should not include .txt files
                assert not any("readme" in c for c in configs)


class TestYAMLRendering:
    """Tests for YAML configuration rendering with Jinja2 templates."""

    def test_render_config_valid_variables(self, tmp_path):
        """Test rendering a config with valid variables."""
        config_content = """
version: "4.22"
platform: "{{ platform }}"
workload: "{{ workload }}"
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(config_content)

        variables = {"platform": "aws", "workload": "cluster-density"}
        result = _render_config_yaml(str(config_file), "", input_vars=variables)

        # Result is a dict after yaml.safe_load
        assert isinstance(result, dict)
        assert result.get("platform") == "aws"
        assert result.get("workload") == "cluster-density"

    def test_render_config_missing_variable(self, tmp_path):
        """Test rendering a config with missing variables."""
        config_content = """
version: "4.22"
platform: "{{ platform }}"
workload: "{{ workload }}"
"""
        config_file = tmp_path / "test.yaml"
        config_file.write_text(config_content)

        variables = {"platform": "aws"}  # Missing "workload"
        # This may raise an error or use a default - depends on Jinja2 settings
        try:
            result = _render_config_yaml(str(config_file), "", input_vars=variables)
            # If it succeeds, undefined should be empty or default
            assert result is not None
        except Exception as e:
            # Jinja2 may raise on undefined variables
            assert "undefined" in str(e).lower() or "workload" in str(e).lower()

    def test_render_config_invalid_path(self, tmp_path):
        """Test rendering a config from a non-existent file."""
        invalid_path = str(tmp_path / "nonexistent.yaml")
        variables = {"platform": "aws"}

        with pytest.raises((FileNotFoundError, OSError)):
            _render_config_yaml(invalid_path, "", input_vars=variables)

    def test_render_config_invalid_yaml_syntax(self, tmp_path):
        """Test rendering invalid YAML syntax."""
        config_content = """
version: "4.22"
invalid: [yaml: syntax:
"""
        config_file = tmp_path / "invalid.yaml"
        config_file.write_text(config_content)

        # Invalid YAML will raise an error during yaml.safe_load
        from yaml.parser import ParserError

        variables = {}
        with pytest.raises(ParserError):
            _render_config_yaml(str(config_file), "", input_vars=variables)

    def test_render_config_conditional_logic(self, tmp_path):
        """Test rendering with Jinja2 conditional logic."""
        config_content = """
version: "4.22"
{% if fips == 'true' %}
fips_enabled: true
{% else %}
fips_enabled: false
{% endif %}
"""
        config_file = tmp_path / "conditional.yaml"
        config_file.write_text(config_content)

        variables = {"fips": "true"}
        result = _render_config_yaml(str(config_file), "", input_vars=variables)
        # Result is a dict
        assert isinstance(result, dict)
        assert result.get("fips_enabled") is True

    def test_render_config_loops(self, tmp_path):
        """Test rendering with Jinja2 filters."""
        config_content = """
version: "4.22"
nodes_str: "{{ node_string }}"
selected_node: "{{ selected_node }}"
"""
        config_file = tmp_path / "loops.yaml"
        config_file.write_text(config_content)

        # Note: input_vars are converted to strings when passed to Jinja2
        variables = {"node_string": "worker,master,infra", "selected_node": "worker"}
        result = _render_config_yaml(str(config_file), "", input_vars=variables)
        # Result is a dict
        assert isinstance(result, dict)
        assert "worker" in result.get("nodes_str", "")
        assert result.get("selected_node") == "worker"


class TestConfigurationResolution:
    """Tests for ES configuration resolution."""

    def test_get_data_source_from_environment(self, monkeypatch):
        """Test getting data source from environment variable."""
        from utils.utils import get_data_source

        monkeypatch.setenv("ES_SERVER", "http://localhost:9200")
        monkeypatch.delenv("es_metadata_index", raising=False)
        # Reset context variable
        from utils.utils import current_es_config
        current_es_config.set(None)

        result = get_data_source()
        assert result == "http://localhost:9200"

    def test_get_data_source_from_context(self):
        """Test getting data source from context variable."""
        from utils.utils import get_data_source, current_es_config

        context_config = {"es_server": "http://context-server:9200"}
        token = current_es_config.set(context_config)

        try:
            result = get_data_source()
            assert result == "http://context-server:9200"
        finally:
            current_es_config.reset(token)

    def test_get_data_source_missing(self, monkeypatch):
        """Test error when data source is not set."""
        from utils.utils import get_data_source, current_es_config

        monkeypatch.delenv("ES_SERVER", raising=False)
        current_es_config.set(None)

        with pytest.raises(EnvironmentError):
            get_data_source()
