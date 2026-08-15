"""Tests for app/config.py -- the app's one os.environ touchpoint."""

from app.config import get_configured_provider_name, get_provider_credential


class TestGetConfiguredProviderName:
    def test_defaults_to_csv(self, monkeypatch):
        monkeypatch.delenv("MARKET_DATA_PROVIDER", raising=False)
        assert get_configured_provider_name() == "csv"

    def test_reads_the_environment_variable(self, monkeypatch):
        monkeypatch.setenv("MARKET_DATA_PROVIDER", "alpaca")
        assert get_configured_provider_name() == "alpaca"

    def test_normalizes_case_and_whitespace(self, monkeypatch):
        monkeypatch.setenv("MARKET_DATA_PROVIDER", "  Schwab  ")
        assert get_configured_provider_name() == "schwab"


class TestGetProviderCredential:
    def test_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
        assert get_provider_credential("alpaca", "api_key_id") is None

    def test_reads_the_naming_convention(self, monkeypatch):
        monkeypatch.setenv("ALPACA_API_KEY_ID", "abc123")
        assert get_provider_credential("alpaca", "api_key_id") == "abc123"

    def test_empty_string_is_treated_as_unset(self, monkeypatch):
        """An accidentally-blank env var (e.g. `ALPACA_API_KEY_ID=` in
        a .env file) should behave like it's missing, not like a
        provider was configured with an empty credential."""
        monkeypatch.setenv("ALPACA_API_KEY_ID", "")
        assert get_provider_credential("alpaca", "api_key_id") is None
