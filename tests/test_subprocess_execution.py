"""
Tests for subprocess execution utilities (run_command_async, run_orion).

Covers:
- Basic command execution
- Command with environment variables
- Shell execution
- Working directory
- Timeout and cancellation handling
- Non-zero exit codes
- Error handling
"""

import asyncio
import subprocess
import pytest
from unittest.mock import patch

from utils.utils import run_command_async, run_orion


class TestRunCommandAsync:
    """Tests for the run_command_async function."""

    @pytest.mark.asyncio
    async def test_run_command_success(self):
        """Test successful command execution."""
        result = await run_command_async(["echo", "hello"])
        assert result.returncode == 0
        assert "hello" in result.stdout

    @pytest.mark.asyncio
    async def test_run_command_with_env(self):
        """Test command execution with environment variables."""
        result = await run_command_async(
            ["sh", "-c", "echo $TEST_VAR"],
            env={"TEST_VAR": "test_value"}
        )
        assert result.returncode == 0
        assert "test_value" in result.stdout

    @pytest.mark.asyncio
    async def test_run_command_with_cwd(self, tmp_path):
        """Test command execution with working directory."""
        # Create a test file in the temp directory
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")

        result = await run_command_async(
            ["ls"],
            cwd=str(tmp_path)
        )
        assert result.returncode == 0
        assert "test.txt" in result.stdout

    @pytest.mark.asyncio
    async def test_run_command_shell_execution(self):
        """Test shell=True execution."""
        result = await run_command_async(
            "echo hello && echo world",
            shell=True
        )
        assert result.returncode == 0
        assert "hello" in result.stdout
        assert "world" in result.stdout

    @pytest.mark.asyncio
    async def test_run_command_non_zero_exit_code(self):
        """Test handling of non-zero exit codes."""
        result = await run_command_async(["sh", "-c", "exit 42"])
        assert result.returncode == 42

    @pytest.mark.asyncio
    async def test_run_command_stderr(self):
        """Test stderr capture."""
        result = await run_command_async(
            ["sh", "-c", "echo error >&2; exit 1"]
        )
        assert result.returncode == 1
        assert "error" in result.stderr

    @pytest.mark.asyncio
    async def test_run_command_command_not_found(self):
        """Test handling of command not found."""
        result = await run_command_async(["nonexistent_command_12345"])
        assert result.returncode == 1
        # Error message contains either FileNotFoundError or "No such file or directory"
        assert "FileNotFoundError" in result.stderr or "No such file or directory" in result.stderr

    @pytest.mark.asyncio
    async def test_run_command_shell_type_validation(self):
        """Test that shell=True requires string command."""
        # shell=True with list should raise TypeError
        with pytest.raises(TypeError):
            await run_command_async(["echo", "test"], shell=True)

    @pytest.mark.asyncio
    async def test_run_command_exec_type_validation(self):
        """Test that shell=False requires list command."""
        # shell=False with string should raise TypeError
        with pytest.raises(TypeError):
            await run_command_async("echo test", shell=False)

    @pytest.mark.asyncio
    async def test_run_command_timeout_handling(self):
        """Test timeout handling with asyncio.wait_for."""
        # This tests that the command can be cancelled via asyncio
        async def slow_command():
            return await run_command_async(["sleep", "10"])

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(slow_command(), timeout=0.5)

    @pytest.mark.asyncio
    async def test_run_command_large_output(self):
        """Test handling of large stdout/stderr."""
        large_text = "x" * 10000
        result = await run_command_async(
            ["sh", "-c", f"echo {large_text}"]
        )
        assert result.returncode == 0
        assert len(result.stdout) > 1000


class TestRunOrion:
    """Tests for the run_orion function."""

    @pytest.mark.asyncio
    async def test_run_orion_basic_parameters(self):
        """Test that run_orion constructs command correctly."""
        with patch('utils.utils.run_command_async') as mock_run:
            with patch('utils.utils.get_data_source', return_value='http://localhost:9200'):
                with patch('utils.utils.get_es_metadata_index', return_value='perf_scale_ci*'):
                    with patch('utils.utils.get_es_benchmark_index', return_value='perf_scale_results*'):
                        with patch('shutil.which', return_value=None):  # No orion on PATH
                            mock_run.return_value = subprocess.CompletedProcess(
                                args=[],
                                returncode=0,
                                stdout='[]',
                                stderr=''
                            )

                            _ = await run_orion(
                                config="/path/to/config.yaml",
                                version="4.22",
                                lookback="7"
                            )

                            # Verify the function was called
                            assert mock_run.called
                            call_args = mock_run.call_args
                            assert "--lookback" in call_args[0][0]
                            assert "7d" in call_args[0][0]
                            assert "json" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_run_orion_with_input_vars(self):
        """Test run_orion with input variables."""
        with patch('utils.utils.run_command_async') as mock_run:
            with patch('utils.utils.get_data_source', return_value='http://localhost:9200'):
                with patch('utils.utils.get_es_metadata_index', return_value='perf_scale_ci*'):
                    with patch('utils.utils.get_es_benchmark_index', return_value='perf_scale_results*'):
                        with patch('shutil.which', return_value=None):
                            mock_run.return_value = subprocess.CompletedProcess(
                                args=[],
                                returncode=0,
                                stdout='[]',
                                stderr=''
                            )

                            input_vars = {"platform": "aws", "workload": "cluster-density"}
                            _ = await run_orion(
                                config="/path/to/config.yaml",
                                version="4.22",
                                lookback="7",
                                input_vars=input_vars
                            )

                            call_args = mock_run.call_args
                            assert "--input-vars" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_run_orion_missing_data_source(self):
        """Test run_orion raises ValueError when data source is not set."""
        with patch('utils.utils.get_data_source', side_effect=ValueError("Data source is not set")):
            with pytest.raises(ValueError):
                await run_orion(
                    config="/path/to/config.yaml",
                    version="4.22",
                    lookback="7"
                )

    @pytest.mark.asyncio
    async def test_run_orion_jira_options(self):
        """Test run_orion with JIRA options."""
        with patch('utils.utils.run_command_async') as mock_run:
            with patch('utils.utils.get_data_source', return_value='http://localhost:9200'):
                with patch('utils.utils.get_es_metadata_index', return_value='perf_scale_ci*'):
                    with patch('utils.utils.get_es_benchmark_index', return_value='perf_scale_results*'):
                        with patch('shutil.which', return_value=None):
                            mock_run.return_value = subprocess.CompletedProcess(
                                args=[],
                                returncode=0,
                                stdout='[]',
                                stderr=''
                            )

                            _ = await run_orion(
                                config="/path/to/config.yaml",
                                version="4.22",
                                lookback="7",
                                jira_ack=True,
                                jira_status_filter="Done"
                            )

                            call_args = mock_run.call_args
                            assert "--jira-ack" in call_args[0][0]
                            assert "--jira-status-filter" in call_args[0][0]
                            assert "Done" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_run_orion_pr_analysis(self):
        """Test run_orion with PR analysis options."""
        with patch('utils.utils.run_command_async') as mock_run:
            with patch('utils.utils.get_data_source', return_value='http://localhost:9200'):
                with patch('utils.utils.get_es_metadata_index', return_value='perf_scale_ci*'):
                    with patch('utils.utils.get_es_benchmark_index', return_value='perf_scale_results*'):
                        with patch('shutil.which', return_value=None):
                            mock_run.return_value = subprocess.CompletedProcess(
                                args=[],
                                returncode=0,
                                stdout='[]',
                                stderr=''
                            )

                            _ = await run_orion(
                                config="/path/to/config.yaml",
                                version="4.22",
                                lookback="7",
                                pr_analysis=True,
                                pull_numbers=[123, 456]
                            )

                            call_args = mock_run.call_args
                            assert "--pr-analysis" in call_args[0][0]
                            assert "--pull-number" in call_args[0][0]
