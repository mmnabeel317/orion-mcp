"""
Tests for JSON parsing and error handling.

Covers:
- Valid JSON parsing
- Malformed JSON handling
- Missing fields
- Empty results
- Error responses
"""

import subprocess
import json
import pytest

from utils.utils import summarize_result


class TestSummarizeResult:
    """Tests for the summarize_result function."""

    async def test_summarize_result_valid_json(self):
        """Test summarizing valid Orion JSON output."""
        orion_output = [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "metrics": {
                    "cpu_usage": {"value": 42.5},
                    "memory_usage": {"value": 1024}
                }
            }
        ]
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(orion_output),
            stderr=""
        )

        summary = await summarize_result(result)
        assert "runs" in summary
        assert "cpu_usage" in summary
        assert "memory_usage" in summary
        assert summary["cpu_usage"]["value"] == [42.5]
        assert summary["memory_usage"]["value"] == [1024]

    async def test_summarize_result_multiple_runs(self):
        """Test summarizing multiple runs."""
        orion_output = [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "metrics": {"metric1": {"value": 10}}
            },
            {
                "timestamp": "2026-01-02T00:00:00Z",
                "metrics": {"metric1": {"value": 20}}
            }
        ]
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(orion_output),
            stderr=""
        )

        summary = await summarize_result(result)
        assert summary["metric1"]["value"] == [10, 20]

    async def test_summarize_result_empty_json(self):
        """Test handling of empty JSON array."""
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps([]),
            stderr=""
        )

        summary = await summarize_result(result)
        assert summary == {}

    async def test_summarize_result_exit_code_3(self):
        """Test handling of exit code 3 (no data)."""
        result = subprocess.CompletedProcess(
            args=[],
            returncode=3,
            stdout="",
            stderr=""
        )

        summary = await summarize_result(result)
        assert summary == {}

    async def test_summarize_result_malformed_json(self):
        """Test handling of malformed JSON."""
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="{invalid json",
            stderr=""
        )

        summary = await summarize_result(result)
        assert isinstance(summary, str)
        assert "Error" in summary

    async def test_summarize_result_non_json_output(self):
        """Test handling of non-JSON output."""
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="Not JSON at all",
            stderr=""
        )

        summary = await summarize_result(result)
        assert isinstance(summary, str)
        assert "Error" in summary

    async def test_summarize_result_isolate_metric(self):
        """Test isolating a specific metric."""
        orion_output = [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "metrics": {
                    "cpu_usage": {"value": 42.5},
                    "memory_usage": {"value": 1024}
                }
            }
        ]
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(orion_output),
            stderr=""
        )

        summary = await summarize_result(result, isolate="cpu_usage")
        assert "cpu_usage" in summary
        assert "memory_usage" not in summary
        assert summary["cpu_usage"]["value"] == [42.5]

    async def test_summarize_result_timestamp_preserved(self):
        """Test that timestamps are preserved in summary."""
        orion_output = [
            {
                "timestamp": "2026-01-01T12:34:56Z",
                "metrics": {"metric1": {"value": 100}}
            }
        ]
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(orion_output),
            stderr=""
        )

        summary = await summarize_result(result)
        assert summary["timestamp"] == "2026-01-01T12:34:56Z"

    async def test_summarize_result_missing_metrics_field(self):
        """Test handling of missing metrics field."""
        orion_output = [
            {
                "timestamp": "2026-01-01T00:00:00Z"
                # Missing "metrics" field
            }
        ]
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(orion_output),
            stderr=""
        )

        summary = await summarize_result(result)
        assert isinstance(summary, str)
        assert "Error" in summary

    async def test_summarize_result_non_zero_exit_code(self):
        """Test handling of non-zero exit code (not 3)."""
        result = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout='[{"timestamp": "2026-01-01T00:00:00Z", "metrics": {}}]',
            stderr="Some error"
        )

        summary = await summarize_result(result)
        # Should still try to parse the stdout
        assert "runs" in summary
