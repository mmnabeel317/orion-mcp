"""
Shared test fixtures and configuration for the Orion MCP test suite.
"""

import pytest


@pytest.fixture
def mock_es_environment(monkeypatch):
    """Fixture that sets up a mock ES environment."""
    monkeypatch.setenv("ES_SERVER", "http://localhost:9200")
    monkeypatch.setenv("es_metadata_index", "perf_scale_ci*")
    monkeypatch.setenv("es_benchmark_index", "perf_scale_results*")
    return {
        "es_server": "http://localhost:9200",
        "es_metadata_index": "perf_scale_ci*",
        "es_benchmark_index": "perf_scale_results*"
    }


@pytest.fixture
def clean_context():
    """Fixture that resets the context variable."""
    from utils.utils import current_es_config
    # Save the token from setting to None
    token = current_es_config.set(None)
    yield
    # Restore using the token
    current_es_config.reset(token)


@pytest.fixture
def mock_orion_output():
    """Fixture that provides mock Orion output."""
    return [
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "metrics": {
                "cpu_usage": {"value": 42.5},
                "memory_usage": {"value": 1024},
                "network_latency": {"value": 5.2}
            }
        },
        {
            "timestamp": "2026-01-02T00:00:00Z",
            "metrics": {
                "cpu_usage": {"value": 45.2},
                "memory_usage": {"value": 1050},
                "network_latency": {"value": 5.5}
            }
        }
    ]


@pytest.fixture
def mock_prow_response():
    """Fixture that provides mock Prow API response."""
    return {
        "results": [
            {
                "metadata": {
                    "name": "periodic-ci-test",
                    "labels": {
                        "version": "4.22",
                        "platform": "aws",
                        "workload": "cluster-density",
                        "scale": "medium"
                    }
                }
            },
            {
                "metadata": {
                    "name": "periodic-ci-test-2",
                    "labels": {
                        "version": "4.22",
                        "platform": "gcp",
                        "workload": "node-density"
                    }
                }
            }
        ]
    }


@pytest.fixture
def sample_config_yaml():
    """Fixture that provides a sample config YAML."""
    return """
version: "4.22"
metadata:
  platform: "{{ platform }}"
  workload: "{{ workload }}"
  scale: "{{ scale | default('medium') }}"

metrics:
  - cpu_usage
  - memory_usage
  - network_latency

thresholds:
  cpu_usage: 50.0
  memory_usage: 2048
"""


# pytest configuration is handled via pytest.ini or pyproject.toml
