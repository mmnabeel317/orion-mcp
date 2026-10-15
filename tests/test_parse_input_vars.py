"""Unit tests for _parse_input_vars in orion_mcp.py.

Tests run without network access, credentials, or an Orion subprocess.
Module-level calls to list_orion_configs() are mocked during import.
"""

from unittest.mock import patch

import pytest

# Mock list_orion_configs before importing the module, since
# orion_mcp.py calls it at module level (ORION_CONFIGS = list_orion_configs()).
with patch("utils.utils.list_orion_configs", return_value=[]):
    from orion_mcp import _parse_input_vars


class TestParseInputVarsValidObjects:
    """Valid JSON objects should be accepted and returned as dicts."""

    def test_valid_object_returns_dict(self):
        """Simple one-key object parses to the expected dict."""
        assert _parse_input_vars('{"key": "val"}') == {"key": "val"}

    def test_empty_object_returns_dict(self):
        """An empty JSON object parses to an empty dict."""
        assert _parse_input_vars("{}") == {}

    def test_nested_object_returns_dict(self):
        """Nested objects and arrays inside the top-level object are preserved."""
        result = _parse_input_vars('{"a": {"b": 1}, "c": [1, 2]}')
        assert result == {"a": {"b": 1}, "c": [1, 2]}


class TestParseInputVarsEmptyInput:
    """Empty input should return None."""

    def test_empty_string_returns_none(self):
        """An empty string is treated as 'not provided' and returns None."""
        assert _parse_input_vars("") is None

    def test_none_input_returns_none(self):
        """None is treated as 'not provided' and returns None."""
        assert _parse_input_vars(None) is None


class TestParseInputVarsNonObjectJson:  # pylint: disable=too-few-public-methods
    """Valid JSON that is not an object must raise ValueError."""

    @pytest.mark.parametrize(
        "bad_input",
        ["[]", '"hello"', "42", "true", "null"],
        ids=["array", "string", "number", "boolean", "null"],
    )
    def test_non_object_json_raises_value_error(self, bad_input):
        """Each non-object JSON type is rejected with a clear error."""
        with pytest.raises(ValueError, match="Non-object input_vars JSON"):
            _parse_input_vars(bad_input)


class TestParseInputVarsMalformedJson:
    """Malformed JSON must raise ValueError with the existing error message."""

    def test_malformed_json_raises_value_error(self):
        """Truncated JSON raises ValueError with the original error message."""
        with pytest.raises(ValueError, match="Malformed input_vars JSON"):
            _parse_input_vars("{bad")

    def test_trailing_comma_raises_value_error(self):
        """A trailing comma is invalid JSON and raises ValueError."""
        with pytest.raises(ValueError, match="Malformed input_vars JSON"):
            _parse_input_vars('{"key": "val",}')
