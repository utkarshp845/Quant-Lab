"""Tests for app/features/engine.py::compute_features() -- the pure
per-bar orchestrator tying every feature category together. No
database, no HTTP -- synthetic HistoricalBar lists only.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.market_data import HistoricalBar
from app.features.engine import compute_features


def _bar(symbol, ts, close, volume=1_000) -> HistoricalBar:
    return HistoricalBar(
        symbol=symbol, timestamp=ts, open=close, high=close + 0.5, low=close - 0.5, close=close, volume=volume,
        provider="csv", timeframe="5m",
    )


def _series(symbol, closes: list[float], *, start=None) -> list[HistoricalBar]:
    start = start or datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    return [_bar(symbol, start + timedelta(minutes=5 * i), c) for i, c in enumerate(closes)]


CALCULATED_AT = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


class TestOneRecordPerBar:
    def test_produces_exactly_one_feature_record_per_bar_in_order(self):
        bars = _series("TSLA", [100.0, 101.0, 102.0])
        records = compute_features(symbol="TSLA", timeframe="5m", provider="csv", bars=bars, calculated_at=CALCULATED_AT)

        assert len(records) == 3
        assert [r.timestamp for r in records] == [b.timestamp for b in bars]

    def test_empty_bars_produces_no_records(self):
        records = compute_features(symbol="TSLA", timeframe="5m", provider="csv", bars=[], calculated_at=CALCULATED_AT)
        assert records == []


class TestMetadataPreservation:
    """Rule 7: preserve symbol, timestamp, timeframe, and
    feature-calculation metadata."""

    def test_symbol_timeframe_provider_and_calculated_at_are_carried_through(self):
        bars = _series("tsla", [100.0])  # lower-case on purpose
        records = compute_features(symbol="tsla", timeframe="5m", provider="alpaca", bars=bars, calculated_at=CALCULATED_AT)

        record = records[0]
        assert record.symbol == "TSLA"  # normalized upper-case, same convention as HistoricalBar-adjacent models
        assert record.timeframe == "5m"
        assert record.provider == "alpaca"
        assert record.calculated_at == CALCULATED_AT
        assert record.timestamp == bars[0].timestamp
        assert record.feature_contract_version == "v1"


class TestMarketContextEligibility:
    def test_tsla_receives_market_context_when_configured(self):
        asset = _series("TSLA", [100.0] * 6 + [98.0])
        spy = _series("SPY", [500.0] * 6 + [505.0])
        qqq = _series("QQQ", [400.0] * 6 + [396.0])

        records = compute_features(
            symbol="TSLA", timeframe="5m", provider="csv", bars=asset, calculated_at=CALCULATED_AT,
            spy_bars=spy, qqq_bars=qqq, market_context_symbols={"TSLA", "NVDA"},
        )

        last = records[-1]
        assert last.market_context is not None
        assert last.market_context.spy_return_5m == pytest.approx(0.01)
        assert last.market_context.qqq_return_5m == pytest.approx(-0.01)

    def test_nvda_receives_market_context_when_configured(self):
        asset = _series("NVDA", [200.0] * 6 + [196.0])
        spy = _series("SPY", [500.0] * 7)

        records = compute_features(
            symbol="NVDA", timeframe="5m", provider="csv", bars=asset, calculated_at=CALCULATED_AT,
            spy_bars=spy, qqq_bars=[], market_context_symbols={"TSLA", "NVDA"},
        )

        assert records[-1].market_context is not None

    def test_mcl_does_not_receive_market_context_unless_explicitly_configured(self):
        asset = _series("MCL", [80.0] * 6 + [78.0])
        spy = _series("SPY", [500.0] * 7)
        qqq = _series("QQQ", [400.0] * 7)

        records = compute_features(
            symbol="MCL", timeframe="5m", provider="csv", bars=asset, calculated_at=CALCULATED_AT,
            spy_bars=spy, qqq_bars=qqq, market_context_symbols={"TSLA", "NVDA"},  # MCL is not a member
        )

        assert all(r.market_context is None for r in records)

    def test_mcl_can_receive_market_context_when_explicitly_configured(self):
        """"...unless explicitly configured" -- MCL CAN opt in, via the
        caller's own market_context_symbols set."""
        asset = _series("MCL", [80.0] * 6 + [78.0])
        spy = _series("SPY", [500.0] * 6 + [505.0])

        records = compute_features(
            symbol="MCL", timeframe="5m", provider="csv", bars=asset, calculated_at=CALCULATED_AT,
            spy_bars=spy, qqq_bars=[], market_context_symbols={"MCL"},
        )

        assert records[-1].market_context is not None
        assert records[-1].market_context.spy_return_5m == pytest.approx(0.01)

    def test_no_market_context_symbols_configured_at_all_means_nobody_gets_it(self):
        """The pure engine's own default -- see its module docstring:
        an empty market_context_symbols means no symbol is eligible,
        even TSLA."""
        asset = _series("TSLA", [100.0] * 6 + [98.0])
        spy = _series("SPY", [500.0] * 7)

        records = compute_features(
            symbol="TSLA", timeframe="5m", provider="csv", bars=asset, calculated_at=CALCULATED_AT, spy_bars=spy
        )

        assert all(r.market_context is None for r in records)

    def test_eligible_symbol_with_no_spy_qqq_data_gets_an_all_none_but_present_submodel(self):
        asset = _series("TSLA", [100.0] * 6 + [98.0])

        records = compute_features(
            symbol="TSLA", timeframe="5m", provider="csv", bars=asset, calculated_at=CALCULATED_AT,
            market_context_symbols={"TSLA"},
        )

        last = records[-1]
        assert last.market_context is not None  # configured -- present, just empty
        assert last.market_context.spy_return_5m is None


class TestNoLookAheadBehavior:
    def test_perturbing_a_future_bar_does_not_change_an_earlier_records_features(self):
        base_closes = [100.0] * 6 + [98.0, 97.9, 97.8, 97.0, 100.0, 100.0, 98.0, 98.5, 99.0, 99.8]
        asset = _series("TSLA", base_closes)
        spy = _series("SPY", [500.0 + i * 0.1 for i in range(len(base_closes))])

        original = compute_features(
            symbol="TSLA", timeframe="5m", provider="csv", bars=asset, calculated_at=CALCULATED_AT,
            spy_bars=spy, market_context_symbols={"TSLA"},
        )

        perturbed_closes = list(base_closes)
        perturbed_spy_closes = [500.0 + i * 0.1 for i in range(len(base_closes))]
        target_index = 5  # somewhere in the middle
        for i in range(target_index + 5, len(perturbed_closes)):  # perturb everything well after it
            perturbed_closes[i] = 12345.0
            perturbed_spy_closes[i] = 999.0
        perturbed_asset = _series("TSLA", perturbed_closes)
        perturbed_spy = _series("SPY", perturbed_spy_closes)

        perturbed = compute_features(
            symbol="TSLA", timeframe="5m", provider="csv", bars=perturbed_asset, calculated_at=CALCULATED_AT,
            spy_bars=perturbed_spy, market_context_symbols={"TSLA"},
        )

        assert original[target_index].price == perturbed[target_index].price
        assert original[target_index].volume == perturbed[target_index].volume
        assert original[target_index].volatility == perturbed[target_index].volatility
        assert original[target_index].price_position == perturbed[target_index].price_position
        # SPY at the target's own timestamp was left unperturbed
        # (only LATER SPY bars changed), so market context is
        # unaffected too.
        assert original[target_index].market_context == perturbed[target_index].market_context

    def test_truncating_the_dataset_after_a_given_bar_reproduces_that_bars_record_identically(self):
        closes = [100.0] * 6 + [98.0, 97.9, 97.8, 97.0, 100.0]
        asset = _series("TSLA", closes)

        full = compute_features(symbol="TSLA", timeframe="5m", provider="csv", bars=asset, calculated_at=CALCULATED_AT)
        truncated_asset = _series("TSLA", closes[:7])
        truncated = compute_features(
            symbol="TSLA", timeframe="5m", provider="csv", bars=truncated_asset, calculated_at=CALCULATED_AT
        )

        assert truncated[-1] == full[6]


class TestReproducibility:
    def test_computing_the_same_inputs_twice_produces_identical_records(self):
        asset = _series("TSLA", [100.0] * 6 + [98.0, 97.5, 97.0])
        spy = _series("SPY", [500.0] * 9)

        first = compute_features(
            symbol="TSLA", timeframe="5m", provider="csv", bars=asset, calculated_at=CALCULATED_AT,
            spy_bars=spy, market_context_symbols={"TSLA"},
        )
        second = compute_features(
            symbol="TSLA", timeframe="5m", provider="csv", bars=asset, calculated_at=CALCULATED_AT,
            spy_bars=spy, market_context_symbols={"TSLA"},
        )

        assert first == second

    def test_repeated_calls_do_not_mutate_the_input_bars(self):
        asset = _series("TSLA", [100.0] * 10)
        snapshot = list(asset)

        for _ in range(3):
            compute_features(symbol="TSLA", timeframe="5m", provider="csv", bars=asset, calculated_at=CALCULATED_AT)

        assert asset == snapshot
