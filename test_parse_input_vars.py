"""
Focused unit tests for _parse_input_vars() in orion_mcp.

These tests run without network access, credentials, or an Orion subprocess.
Module-level config discovery (list_orion_configs) degrades gracefully when
the GitHub API and local /orion/examples/ path are both unavailable, so no
patching is needed.
"""

import pytest

from orion_mcp import _parse_input_vars

# ---------------------------------------------------------------------------
# Edge inputs — returns None
# ---------------------------------------------------------------------------


def test_empty_string_returns_none():
    """An empty string means the caller did not supply input_vars."""
    assert _parse_input_vars("") is None


# ---------------------------------------------------------------------------
# Valid inputs — should return a dict
# ---------------------------------------------------------------------------


def test_valid_object_returns_dict():
    """A well-formed JSON object is returned as a Python dict."""
    result = _parse_input_vars('{"platform": "aws", "workerNodesCount": 3}')
    assert result == {"platform": "aws", "workerNodesCount": 3}


def test_empty_object_returns_empty_dict():
    """An empty JSON object {} is valid and returns {}."""
    result = _parse_input_vars("{}")
    assert result == {}


def test_nested_object_returns_dict():
    """Nested JSON objects are accepted and returned as nested dicts."""
    result = _parse_input_vars('{"outer": {"inner": 1}}')
    assert result == {"outer": {"inner": 1}}


# ---------------------------------------------------------------------------
# Invalid inputs — non-object JSON must raise ValueError
# ---------------------------------------------------------------------------


def test_json_array_raises_value_error():
    """A JSON array is not a valid input_vars value."""
    with pytest.raises(ValueError, match="input_vars JSON must be an object"):
        _parse_input_vars("[]")


def test_json_array_with_items_raises_value_error():
    """A non-empty JSON array is still rejected."""
    with pytest.raises(ValueError, match="input_vars JSON must be an object"):
        _parse_input_vars('["a", "b"]')


def test_json_string_raises_value_error():
    """A bare JSON string is not an object."""
    with pytest.raises(ValueError, match="input_vars JSON must be an object"):
        _parse_input_vars('"hello"')


def test_json_integer_raises_value_error():
    """A JSON number is not an object."""
    with pytest.raises(ValueError, match="input_vars JSON must be an object"):
        _parse_input_vars("42")


def test_json_float_raises_value_error():
    """A JSON float is not an object."""
    with pytest.raises(ValueError, match="input_vars JSON must be an object"):
        _parse_input_vars("3.14")


def test_json_true_raises_value_error():
    """JSON true is not an object."""
    with pytest.raises(ValueError, match="input_vars JSON must be an object"):
        _parse_input_vars("true")


def test_json_false_raises_value_error():
    """JSON false is not an object."""
    with pytest.raises(ValueError, match="input_vars JSON must be an object"):
        _parse_input_vars("false")


def test_json_null_raises_value_error():
    """JSON null is not an object."""
    with pytest.raises(ValueError, match="input_vars JSON must be an object"):
        _parse_input_vars("null")


# ---------------------------------------------------------------------------
# Malformed JSON — must continue to raise ValueError
# ---------------------------------------------------------------------------


def test_whitespace_only_raises_value_error():
    """Whitespace-only strings are invalid JSON and should raise ValueError."""
    with pytest.raises(ValueError, match="Malformed input_vars JSON"):
        _parse_input_vars("   ")


def test_malformed_json_raises_value_error():
    """Malformed JSON raises ValueError with a descriptive message."""
    with pytest.raises(ValueError, match="Malformed input_vars JSON"):
        _parse_input_vars("{not valid json}")


def test_truncated_json_raises_value_error():
    """An incomplete JSON value raises ValueError."""
    with pytest.raises(ValueError, match="Malformed input_vars JSON"):
        _parse_input_vars('{"key":')


def test_plain_text_raises_value_error():
    """Plain text (not JSON at all) raises ValueError."""
    with pytest.raises(ValueError, match="Malformed input_vars JSON"):
        _parse_input_vars("just text")
