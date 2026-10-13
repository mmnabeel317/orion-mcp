"""Unit tests for utils/header_decryption.py."""

import base64
import os

import pytest

from utils.header_decryption import (
    HEADER_NAME,
    decrypt_payload,
    get_decrypted_context,
    get_es_config_from_headers,
)


class TestDecryptPayload:
    """decrypt_payload() decrypts base64(nonce + ciphertext + tag) blobs."""

    def test_round_trip(self, symmetric_key, encrypt):
        """A blob encrypted with the shared key decrypts to the original text."""
        blob = encrypt('{"es_server": "https://es"}', symmetric_key)
        assert decrypt_payload(blob) == '{"es_server": "https://es"}'

    def test_missing_key_env(self, encrypt):
        """An unset HEADER_SYMMETRIC_KEY is rejected."""
        blob = encrypt("x", os.urandom(32))
        with pytest.raises(ValueError, match="HEADER_SYMMETRIC_KEY environment variable not set"):
            decrypt_payload(blob)

    def test_invalid_base64_key(self, monkeypatch, encrypt):
        """A key that is not valid base64 is rejected."""
        monkeypatch.setenv("HEADER_SYMMETRIC_KEY", "a")
        with pytest.raises(ValueError, match="must be base64"):
            decrypt_payload(encrypt("x", os.urandom(32)))

    def test_wrong_key_length(self, monkeypatch, encrypt):
        """A key that does not decode to 32 bytes is rejected."""
        monkeypatch.setenv("HEADER_SYMMETRIC_KEY", base64.b64encode(os.urandom(16)).decode())
        with pytest.raises(ValueError, match="32 bytes"):
            decrypt_payload(encrypt("x", os.urandom(32)))

    @pytest.mark.usefixtures("symmetric_key")
    def test_invalid_base64_blob(self):
        """An encrypted blob that is not valid base64 is rejected."""
        with pytest.raises(ValueError, match="Invalid encrypted blob format"):
            decrypt_payload("a")

    @pytest.mark.usefixtures("symmetric_key")
    def test_blob_too_short(self):
        """A blob shorter than the 12-byte nonce is rejected."""
        with pytest.raises(ValueError, match="too short"):
            decrypt_payload(base64.b64encode(b"short").decode())

    @pytest.mark.usefixtures("symmetric_key")
    def test_wrong_key_fails_authentication(self, encrypt):
        """A blob encrypted with a different key fails to decrypt."""
        blob = encrypt("secret", os.urandom(32))
        with pytest.raises(ValueError, match="Decryption failed"):
            decrypt_payload(blob)

    def test_tampered_ciphertext_fails(self, symmetric_key, encrypt):
        """Flipping a ciphertext byte fails GCM authentication."""
        raw = bytearray(base64.b64decode(encrypt("secret", symmetric_key)))
        raw[-1] ^= 0x01
        with pytest.raises(ValueError, match="Decryption failed"):
            decrypt_payload(base64.b64encode(bytes(raw)).decode())


class TestGetDecryptedContext:
    """get_decrypted_context() finds, decrypts, and parses the context header."""

    def test_empty_headers(self):
        """No headers at all yields None."""
        assert get_decrypted_context({}) is None
        assert get_decrypted_context(None) is None

    def test_header_absent(self):
        """Headers without the encrypted context yield None."""
        assert get_decrypted_context({"Content-Type": "application/json"}) is None

    def test_empty_header_value(self):
        """An empty header value is treated as absent."""
        assert get_decrypted_context({HEADER_NAME: ""}) is None

    @pytest.mark.parametrize("header_name", [HEADER_NAME, HEADER_NAME.lower(), HEADER_NAME.upper()])
    def test_case_insensitive_lookup(self, symmetric_key, encrypt, header_name):
        """The header name is matched case-insensitively."""
        payload = {"es_server": "https://es", "api_key": "k"}
        headers = {"Accept": "*/*", header_name: encrypt(payload, symmetric_key)}
        assert get_decrypted_context(headers) == payload

    def test_non_dict_json_rejected(self, symmetric_key, encrypt):
        """Valid JSON that is not an object is rejected."""
        headers = {HEADER_NAME: encrypt("[1, 2]", symmetric_key)}
        with pytest.raises(ValueError, match="Expected dict"):
            get_decrypted_context(headers)

    def test_invalid_json_rejected(self, symmetric_key, encrypt):
        """Decrypted text that is not JSON is rejected."""
        headers = {HEADER_NAME: encrypt("not json", symmetric_key)}
        with pytest.raises(ValueError, match="Invalid context JSON format"):
            get_decrypted_context(headers)

    @pytest.mark.usefixtures("symmetric_key")
    def test_decryption_failure_wrapped(self, encrypt):
        """Decryption errors are wrapped as 'Invalid encrypted context'."""
        headers = {HEADER_NAME: encrypt({"a": 1}, os.urandom(32))}
        with pytest.raises(ValueError, match="Invalid encrypted context"):
            get_decrypted_context(headers)


class TestGetEsConfigFromHeaders:
    """get_es_config_from_headers() extracts only ES-related fields."""

    def test_no_header(self):
        """Without the encrypted header, None is returned."""
        assert get_es_config_from_headers({"Accept": "*/*"}) is None

    def test_extracts_es_fields_only(self, symmetric_key, encrypt):
        """Only es_* fields are returned; other secrets are filtered out."""
        payload = {
            "es_server": "https://es",
            "es_metadata_index": "meta*",
            "es_benchmark_index": "bench*",
            "api_key": "do-not-leak",
        }
        config = get_es_config_from_headers({HEADER_NAME: encrypt(payload, symmetric_key)})
        assert config == {
            "es_server": "https://es",
            "es_metadata_index": "meta*",
            "es_benchmark_index": "bench*",
        }

    def test_optional_fields_omitted(self, symmetric_key, encrypt):
        """Optional index fields are omitted when not supplied."""
        config = get_es_config_from_headers({HEADER_NAME: encrypt({"es_server": "https://es"}, symmetric_key)})
        assert config == {"es_server": "https://es"}

    def test_missing_es_server(self, symmetric_key, encrypt):
        """A context without es_server is rejected."""
        headers = {HEADER_NAME: encrypt({"es_metadata_index": "meta*"}, symmetric_key)}
        with pytest.raises(ValueError, match="missing required field 'es_server'"):
            get_es_config_from_headers(headers)
