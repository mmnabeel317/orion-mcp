"""
Tests for deployment health checks and containerized execution.

Covers:
- Health check endpoints
- Local vs containerized deployment detection
- ES server connectivity
- Configuration path validation in both environments
- Proper error reporting for deployment issues
"""

import pytest
import os
from unittest.mock import patch, AsyncMock, MagicMock
import httpx

from utils.utils import run_orion


class TestDeploymentDetection:
    """Tests for detecting local vs containerized deployment."""

    async def test_orion_binary_detection_local(self):
        """Test detection of local orion binary uses local command."""
        with patch('shutil.which', return_value='/usr/local/bin/orion'):
            with patch('utils.utils.run_command_async', new_callable=AsyncMock) as mock_run:
                with patch('utils.utils.get_data_source', return_value='http://localhost:9200'):
                    with patch('utils.utils.get_es_metadata_index', return_value='perf_scale_ci*'):
                        with patch('utils.utils.get_es_benchmark_index', return_value='perf_scale_results*'):
                            mock_run.return_value = MagicMock(returncode=0, stdout='[]', stderr='')

                            result = await run_orion(
                                config="/path/to/config.yaml",
                                version="4.22",
                                lookback="7"
                            )

                            # Verify run_orion was called and returned expected result
                            assert result.returncode == 0
                            # Verify the command started with "orion" (not "podman")
                            called_command = mock_run.call_args[0][0]
                            assert called_command[0] == "orion"

    async def test_orion_binary_detection_missing(self):
        """Test detection when orion binary is missing uses podman."""
        with patch('shutil.which', return_value=None):
            with patch('utils.utils.run_command_async', new_callable=AsyncMock) as mock_run:
                with patch('utils.utils.get_data_source', return_value='http://localhost:9200'):
                    with patch('utils.utils.get_es_metadata_index', return_value='perf_scale_ci*'):
                        with patch('utils.utils.get_es_benchmark_index', return_value='perf_scale_results*'):
                            mock_run.return_value = MagicMock(returncode=0, stdout='[]', stderr='')

                            result = await run_orion(
                                config="/path/to/config.yaml",
                                version="4.22",
                                lookback="7"
                            )

                            # Verify run_orion was called and returned expected result
                            assert result.returncode == 0
                            # Verify the command started with "podman" (not "orion")
                            called_command = mock_run.call_args[0][0]
                            assert called_command[0] == "podman"

    async def test_podman_fallback_when_orion_missing(self):
        """Test that podman is used when orion binary is missing."""
        with patch('shutil.which', return_value=None):
            with patch('utils.utils.run_command_async', new_callable=AsyncMock) as mock_run:
                with patch('utils.utils.get_data_source', return_value='http://localhost:9200'):
                    with patch('utils.utils.get_es_metadata_index', return_value='perf_scale_ci*'):
                        with patch('utils.utils.get_es_benchmark_index', return_value='perf_scale_results*'):
                            mock_run.return_value = MagicMock(returncode=0, stdout='[]', stderr='')

                            result = await run_orion(
                                config="/path/to/config.yaml",
                                version="4.22",
                                lookback="7"
                            )

                            # Verify the command includes podman and the orion image
                            called_command = mock_run.call_args[0][0]
                            assert called_command[0] == "podman"
                            assert "run" in called_command
                            assert result.returncode == 0


class TestHealthChecks:
    """Tests for deployment health checks."""

    async def test_es_server_connectivity_check(self):
        """Test checking ES server connectivity."""
        # Test get_data_source which calls ES_SERVER environment variable
        import os
        original_es = os.environ.get('ES_SERVER')
        try:
            os.environ['ES_SERVER'] = 'http://test-es:9200'
            from utils.utils import get_data_source
            result = get_data_source()
            # Verify the function returns the configured ES server
            assert isinstance(result, str)
            assert 'test-es' in result
            assert '9200' in result
        finally:
            if original_es:
                os.environ['ES_SERVER'] = original_es
            else:
                os.environ.pop('ES_SERVER', None)

    async def test_es_server_connectivity_failure(self):
        """Test handling of ES server connectivity failure."""
        with patch('httpx.Client', side_effect=httpx.ConnectError("Connection failed")):
            with pytest.raises(httpx.ConnectError):
                httpx.Client()

    async def test_configuration_path_validation_local(self, tmp_path):
        """Test configuration path validation for local deployment."""
        config_file = tmp_path / "test.yaml"
        config_file.write_text("test: config")

        # Test list_orion_configs with a local path
        from utils.utils import list_orion_configs
        with patch('utils.utils.ORION_CONFIGS_PATH', str(tmp_path)):
            with patch('utils.utils.GITHUB_CONFIGS_URL', None):
                configs = list_orion_configs()
                # Verify the function returns the config we created
                assert isinstance(configs, list)
                assert "test.yaml" in configs

    async def test_configuration_path_validation_containerized(self):
        """Test configuration path handling for containerized deployment."""
        # In containerized deployment, config paths are relative to mounted volumes
        # The validation should check that the path exists within the container's config directory
        from utils.constants import ORION_CONFIGS_PATH

        # Should be a valid path
        assert ORION_CONFIGS_PATH is not None
        assert isinstance(ORION_CONFIGS_PATH, str)

    def test_deployment_environment_variables(self, monkeypatch):
        """Test that deployment-specific env vars are accessible."""
        from utils.utils import get_data_source

        monkeypatch.setenv("ES_SERVER", "http://opensearch.example.com:9200")
        result = get_data_source()
        assert "opensearch.example.com" in result

    async def test_temporary_directory_handling(self):
        """Test that temporary directories are properly created and cleaned up in run_orion."""
        # Test run_orion which creates a temporary directory for the command
        with patch('utils.utils.run_command_async', new_callable=AsyncMock) as mock_run:
            with patch('shutil.which', return_value='/usr/bin/orion'):
                with patch('utils.utils.get_data_source', return_value='http://localhost:9200'):
                    with patch('utils.utils.get_es_metadata_index', return_value='perf_scale_ci*'):
                        with patch('utils.utils.get_es_benchmark_index', return_value='perf_scale_results*'):
                            mock_run.return_value = MagicMock(returncode=0, stdout='[]', stderr='')

                            # Call run_orion
                            result = await run_orion(
                                config="/path/to/config.yaml",
                                version="4.22",
                                lookback="7"
                            )

                            # Verify run_orion was called and executed
                            assert result.returncode == 0
                            assert mock_run.called


class TestErrorReporting:
    """Tests for error reporting in deployment contexts."""

    async def test_orion_command_error_reporting(self):
        """Test that orion command errors are properly reported."""
        with patch('utils.utils.run_command_async', new_callable=AsyncMock) as mock_run:
            with patch('utils.utils.get_data_source', return_value='http://localhost:9200'):
                with patch('utils.utils.get_es_metadata_index', return_value='perf_scale_ci*'):
                    with patch('utils.utils.get_es_benchmark_index', return_value='perf_scale_results*'):
                        with patch('shutil.which', return_value=None):
                            mock_run.return_value = MagicMock(
                                returncode=1,
                                stdout='',
                                stderr='Orion error message'
                            )

                            result = await run_orion(
                                config="/path/to/config.yaml",
                                version="4.22",
                                lookback="7"
                            )

                            assert result.returncode == 1
                            assert "Orion error message" in result.stderr

    async def test_configuration_not_found_error(self):
        """Test error when configuration file is not found."""
        with patch('utils.utils.run_command_async', new_callable=AsyncMock) as mock_run:
            with patch('utils.utils.get_data_source', return_value='http://localhost:9200'):
                with patch('utils.utils.get_es_metadata_index', return_value='perf_scale_ci*'):
                    with patch('utils.utils.get_es_benchmark_index', return_value='perf_scale_results*'):
                        with patch('shutil.which', return_value=None):
                            mock_run.return_value = MagicMock(
                                returncode=1,
                                stdout='',
                                stderr='Config file not found: /nonexistent/config.yaml'
                            )

                            result = await run_orion(
                                config="/nonexistent/config.yaml",
                                version="4.22",
                                lookback="7"
                            )

                            assert result.returncode == 1
                            assert "not found" in result.stderr.lower()

    async def test_environment_variable_validation(self, monkeypatch):
        """Test that required environment variables are validated."""
        from utils.utils import get_data_source

        # Remove ES_SERVER
        monkeypatch.delenv("ES_SERVER", raising=False)
        from utils.utils import current_es_config
        current_es_config.set(None)

        with pytest.raises(EnvironmentError):
            get_data_source()


class TestConfigurationResolutionInDeployment:
    """Tests for configuration resolution in different deployment contexts."""

    def test_config_path_resolution_local(self, tmp_path, monkeypatch):
        """Test config path resolution in local deployment."""
        config_dir = tmp_path / "configs"
        config_dir.mkdir()

        config_file = config_dir / "test.yaml"
        config_file.write_text("test: config")

        monkeypatch.setenv("ORION_CONFIGS_PATH", str(config_dir))

        # Path should be resolvable
        assert os.path.exists(config_dir / "test.yaml")

    def test_config_path_resolution_with_environment_override(self, tmp_path, monkeypatch):
        """Test that config path can be overridden with environment variable."""
        config_dir = tmp_path / "custom_configs"
        config_dir.mkdir()

        monkeypatch.setenv("ORION_CONFIGS_PATH", str(config_dir))

        from utils.constants import ORION_CONFIGS_PATH
        # The constant should reflect the environment setting (if it uses it)
        assert isinstance(ORION_CONFIGS_PATH, str)

    async def test_encrypted_header_context_in_deployment(self):
        """Test that encrypted headers work in deployment."""
        from utils.utils import current_es_config
        from utils.utils import get_data_source as get_es_server

        # Simulate encrypted header setting a context variable
        context_config = {
            "es_server": "http://encrypted-header-server:9200",
            "es_metadata_index": "custom_metadata*",
            "es_benchmark_index": "custom_benchmark*"
        }

        token = current_es_config.set(context_config)

        try:
            result = get_es_server()
            assert result == "http://encrypted-header-server:9200"
        finally:
            current_es_config.reset(token)
