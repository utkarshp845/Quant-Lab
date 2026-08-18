"""Tests for app/models/backtesting.py -- request validation and
Backtest.new()'s field assignment. No I/O, no engine, no repository
involved anywhere in this file.
"""

import pytest
from pydantic import ValidationError

from app.models.backtesting import Backtest, BacktestCreateRequest, BacktestStatus, DEFAULT_WINDOWS


class TestBacktestCreateRequestWindows:
    def test_defaults_to_the_specs_five_fifteen_thirty_sixty(self):
        request = BacktestCreateRequest(experiment_id="exp-1")
        assert request.windows == list(DEFAULT_WINDOWS)
        assert request.windows == [5, 15, 30, 60]

    def test_an_explicit_windows_list_is_sorted(self):
        request = BacktestCreateRequest(experiment_id="exp-1", windows=[30, 5, 60])
        assert request.windows == [5, 30, 60]

    def test_an_empty_windows_list_is_rejected(self):
        with pytest.raises(ValidationError):
            BacktestCreateRequest(experiment_id="exp-1", windows=[])

    def test_a_non_positive_window_is_rejected(self):
        with pytest.raises(ValidationError):
            BacktestCreateRequest(experiment_id="exp-1", windows=[5, 0, 15])

        with pytest.raises(ValidationError):
            BacktestCreateRequest(experiment_id="exp-1", windows=[-5, 15])

    def test_duplicate_windows_are_rejected(self):
        with pytest.raises(ValidationError):
            BacktestCreateRequest(experiment_id="exp-1", windows=[5, 15, 5])


class TestBacktestNew:
    def test_assigns_a_fresh_id_and_draft_status(self):
        backtest = Backtest.new(
            experiment_id="exp-1", symbol="TSLA", timeframe="5m", provider="csv", windows=[5, 15], feature_contract_version="v1"
        )

        assert backtest.id
        assert backtest.status == BacktestStatus.DRAFT
        assert backtest.completed_at is None
        assert backtest.results is None
        assert backtest.error_message is None

    def test_two_backtests_get_distinct_ids(self):
        first = Backtest.new(experiment_id="exp-1", symbol="TSLA", timeframe="5m", provider="csv", windows=[5], feature_contract_version="v1")
        second = Backtest.new(experiment_id="exp-1", symbol="TSLA", timeframe="5m", provider="csv", windows=[5], feature_contract_version="v1")

        assert first.id != second.id

    def test_copies_every_field_through_verbatim(self):
        backtest = Backtest.new(
            experiment_id="exp-42", symbol="NVDA", timeframe="1h", provider="alpaca", windows=[5, 60], feature_contract_version="v3"
        )

        assert backtest.experiment_id == "exp-42"
        assert backtest.symbol == "NVDA"
        assert backtest.timeframe == "1h"
        assert backtest.provider == "alpaca"
        assert backtest.windows == [5, 60]
        assert backtest.feature_contract_version == "v3"
