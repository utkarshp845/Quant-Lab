"""Tests for the MarketDataProvider interface (app/providers/).

Covers:
  1. CSVProvider actually satisfies the MarketDataProvider contract
     and returns a NormalizedChainResult with the fields every future
     provider must also fill in correctly.
  2. The registry resolves a known provider name and rejects an
     unknown one, instead of silently returning nothing.
  3. CSVProvider's output is equivalent to calling the underlying
     ingestion module directly -- proving this is a wrapper, not a
     reimplementation (see csv_provider.py's docstring).
  4. The placeholder providers (Alpaca/Massive/Schwab) satisfy the
     interface, fail loudly (not silently) without credentials, and
     raise NotImplementedError rather than returning fake data.
  5. get_default_provider() honors MARKET_DATA_PROVIDER.
  6. The calculation engine has no import-time dependency on CSV
     parsing -- the actual claim behind "the scanner/analysis engine
     must not know data originated from a CSV."
"""

import pathlib
from datetime import date

import pytest

from app.ingestion.csv_normalizer import CsvFormatError, parse_and_normalize_csv
from app.providers.alpaca_provider import AlpacaProvider
from app.providers.base import MarketDataProvider, NormalizedChainResult
from app.providers.csv_provider import CSVProvider
from app.providers.massive_provider import MassiveProvider
from app.providers.registry import PROVIDERS, get_default_provider, get_provider
from app.providers.schwab_provider import SchwabProvider

FIXED_TODAY = date(2026, 8, 19)

VALID_CSV = (
    "Symbol,Underlying Price,Exp Date,Strike,Type,Bid,Ask,Last,Volume,Open Int,Impl Vol,Delta,Gamma,Theta,Vega\n"
    "MCL,82.00,09/18/2026,85,PUT,5.00,5.10,5.05,120,340,44.00%,-0.58,0.05,-0.03,0.12\n"
    "MCL,82.00,09/18/2026,77,PUT,0.71,0.85,0.78,95,210,42.00%,-0.29,0.04,-0.02,0.08\n"
)

MISSING_COLUMN_CSV = "Symbol,Strike,Type,Bid,Ask,Delta\nMCL,85,PUT,5.00,5.10,-0.58\n"


class TestCSVProviderIsAMarketDataProvider:
    def test_is_an_instance_of_the_interface(self):
        assert isinstance(CSVProvider(), MarketDataProvider)

    def test_has_a_name(self):
        assert CSVProvider.name == "csv"

    def test_cannot_instantiate_the_abstract_base_directly(self):
        with pytest.raises(TypeError):
            MarketDataProvider()  # abstract -- get_chain has no implementation


class TestCSVProviderGetChain:
    def test_returns_a_normalized_chain_result(self):
        result = CSVProvider().get_chain(csv_text=VALID_CSV, today=FIXED_TODAY)

        assert isinstance(result, NormalizedChainResult)
        assert result.source == "csv"
        assert result.imported_rows == 2
        assert result.total_rows == 2
        assert result.symbols == ["MCL"]
        assert len(result.contracts) == 2
        assert result.row_errors == []

    def test_metadata_carries_csv_specific_detail(self):
        result = CSVProvider().get_chain(csv_text=VALID_CSV, today=FIXED_TODAY)

        assert "detected_columns" in result.metadata
        assert "column_mapping" in result.metadata
        assert result.metadata["column_mapping"]["symbol"] == "Symbol"

    def test_missing_required_column_raises_csv_format_error(self):
        with pytest.raises(CsvFormatError):
            CSVProvider().get_chain(csv_text=MISSING_COLUMN_CSV, today=FIXED_TODAY)

    def test_output_matches_calling_the_ingestion_module_directly(self):
        """CSVProvider must not reimplement or diverge from
        parse_and_normalize_csv -- it's a wrapper (see csv_provider.py
        docstring), so the two must agree on every contract field."""
        direct = parse_and_normalize_csv(VALID_CSV, today=FIXED_TODAY)
        via_provider = CSVProvider().get_chain(csv_text=VALID_CSV, today=FIXED_TODAY)

        assert via_provider.imported_rows == direct["imported_rows"]
        assert via_provider.symbols == direct["symbols"]
        assert via_provider.expirations_by_symbol == direct["expirations_by_symbol"]
        assert [c.model_dump() for c in via_provider.contracts] == direct["contracts"]


class TestProviderRegistry:
    def test_csv_is_registered(self):
        assert "csv" in PROVIDERS

    def test_get_provider_returns_a_csv_provider_instance(self):
        provider = get_provider("csv")
        assert isinstance(provider, CSVProvider)

    def test_get_provider_rejects_an_unknown_name(self):
        with pytest.raises(ValueError, match="Unknown market data provider"):
            get_provider("robinhood")


class TestPlaceholderProviders:
    """AlpacaProvider, MassiveProvider, SchwabProvider: all three now
    have real get_historical_data()/get_latest_quote() integrations
    (see each provider's dedicated test file); get_chain() (options
    data) remains a placeholder for all three, since that's a
    separate, larger integration none of them have tackled yet. These
    tests lock in the "fail loudly, not silently" contract for
    whatever's still unimplemented, so a future PR that adds
    get_chain() to one of them has a clear point where behavior is
    expected to change (NotImplementedError -> real data)."""

    @pytest.mark.parametrize(
        "provider_cls,expected_name",
        [(AlpacaProvider, "alpaca"), (MassiveProvider, "massive"), (SchwabProvider, "schwab")],
    )
    def test_satisfies_the_interface(self, provider_cls, expected_name):
        provider = provider_cls()
        assert isinstance(provider, MarketDataProvider)
        assert provider.name == expected_name

    @pytest.mark.parametrize("provider_cls", [AlpacaProvider, MassiveProvider, SchwabProvider])
    def test_all_three_are_registered(self, provider_cls):
        assert provider_cls in PROVIDERS.values()

    def test_alpaca_without_credentials_raises_a_clear_error(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
        monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="ALPACA_API_KEY_ID"):
            AlpacaProvider().get_chain()

    def test_alpaca_with_credentials_raises_not_implemented_not_fake_data(self):
        provider = AlpacaProvider(api_key_id="fake", api_secret_key="fake")
        with pytest.raises(NotImplementedError, match="placeholder"):
            provider.get_chain()

    def test_massive_without_credentials_raises_a_clear_error(self, monkeypatch):
        monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="MASSIVE_API_KEY"):
            MassiveProvider().get_chain()

    def test_schwab_without_credentials_raises_a_clear_error(self, monkeypatch):
        monkeypatch.delenv("SCHWAB_CLIENT_ID", raising=False)
        monkeypatch.delenv("SCHWAB_CLIENT_SECRET", raising=False)
        with pytest.raises(RuntimeError, match="SCHWAB_CLIENT_ID"):
            SchwabProvider().get_chain()

    def test_credentials_can_be_passed_explicitly_without_touching_env(self, monkeypatch):
        """Constructor args override env vars -- lets a test (or a
        caller) supply credentials without mutating the environment."""
        monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
        provider = AlpacaProvider(api_key_id="explicit", api_secret_key="explicit")
        assert provider.api_key_id == "explicit"

    def test_schwab_without_refresh_token_raises_a_clear_error(self, monkeypatch):
        monkeypatch.delenv("SCHWAB_REFRESH_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="SCHWAB_REFRESH_TOKEN"):
            SchwabProvider(client_id="id", client_secret="secret").get_chain()

    _REAL_EQUITY_PROVIDERS = [
        (AlpacaProvider, {"api_key_id": "fake", "api_secret_key": "fake"}),
        (MassiveProvider, {"api_key": "fake"}),
        (SchwabProvider, {"client_id": "fake", "client_secret": "fake", "refresh_token": "fake"}),
    ]

    @pytest.mark.parametrize("provider_cls,kwargs", _REAL_EQUITY_PROVIDERS)
    def test_options_chain_still_not_implemented(self, provider_cls, kwargs):
        """get_chain() (options data) is still a placeholder for all
        three real-equity-data providers -- scope for each integration
        was equity data (bars/quotes) only."""
        provider = provider_cls(**kwargs)
        with pytest.raises(NotImplementedError, match="options-chain"):
            provider.get_chain()

    @pytest.mark.parametrize("provider_cls,kwargs", _REAL_EQUITY_PROVIDERS)
    def test_stream_quotes_still_not_implemented(self, provider_cls, kwargs):
        provider = provider_cls(**kwargs)
        with pytest.raises(NotImplementedError):
            provider.stream_quotes()


class TestConfigDrivenProviderSelection:
    def test_defaults_to_csv_when_unset(self, monkeypatch):
        monkeypatch.delenv("MARKET_DATA_PROVIDER", raising=False)
        assert isinstance(get_default_provider(), CSVProvider)

    def test_honors_the_environment_variable(self, monkeypatch):
        monkeypatch.setenv("MARKET_DATA_PROVIDER", "alpaca")
        assert isinstance(get_default_provider(), AlpacaProvider)

    def test_is_case_and_whitespace_insensitive(self, monkeypatch):
        monkeypatch.setenv("MARKET_DATA_PROVIDER", "  ALPACA  ")
        assert isinstance(get_default_provider(), AlpacaProvider)

    def test_unknown_configured_provider_raises_a_clear_error(self, monkeypatch):
        monkeypatch.setenv("MARKET_DATA_PROVIDER", "robinhood")
        with pytest.raises(ValueError, match="Unknown market data provider"):
            get_default_provider()


class TestEngineHasNoCsvDependency:
    """Proves the claim in README section 8 / the provider-refactor
    discussion: the quant engine (calculations/, the bear-put-spread
    request models, and its API route) does not import anything from
    the CSV ingestion or provider layers. If someone adds such an
    import later, this test catches it -- checking source text is
    crude but exact for "does this module import that one," and needs
    no extra tooling.
    """

    ENGINE_FILES = [
        "app/calculations/stats.py",
        "app/calculations/bear_put_spread.py",
        "app/calculations/payoff_scenarios.py",
        "app/calculations/probability_distribution.py",
        "app/calculations/monte_carlo.py",
        "app/models/bear_put_spread.py",
        "app/models/response.py",
        "app/api/bear_put_spread.py",
    ]
    FORBIDDEN_SUBSTRINGS = ["ingestion", "providers", "csv", "Csv", "CSV"]

    def test_no_engine_file_references_csv_or_providers(self):
        backend_root = pathlib.Path(__file__).resolve().parent.parent
        for relative_path in self.ENGINE_FILES:
            source = (backend_root / relative_path).read_text()
            for forbidden in self.FORBIDDEN_SUBSTRINGS:
                assert forbidden not in source, (
                    f"{relative_path} references {forbidden!r} -- the calculation "
                    "engine must depend only on plain request models, never on "
                    "how the data reached them."
                )
