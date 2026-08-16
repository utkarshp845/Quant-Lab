"""Tests for cross_validate_providers.py's one piece of real logic: pct_diff.

The rest of that script is I/O (real HTTP calls, real credentials) --
same as alpaca_manual_check.py / massive_manual_check.py, deliberately
not covered by pytest. pct_diff is a small pure function worth locking
down on its own, specifically the divide-by-zero handling it exists to
avoid crashing on.
"""

import importlib.util
import pathlib

import pytest

_SCRIPT_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "cross_validate_providers.py"
_spec = importlib.util.spec_from_file_location("cross_validate_providers", _SCRIPT_PATH)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
pct_diff = _module.pct_diff


class TestPctDiff:
    def test_identical_values_are_zero_percent(self):
        assert pct_diff(100.0, 100.0) == 0.0

    def test_computes_percentage_difference_relative_to_the_first_value(self):
        assert pct_diff(100.0, 101.0) == pytest.approx(1.0)
        assert pct_diff(200.0, 202.0) == pytest.approx(1.0)

    def test_is_symmetric_in_magnitude_but_relative_to_the_first_argument(self):
        # Relative to a smaller first value, the same absolute gap is a larger percentage.
        assert pct_diff(50.0, 51.0) == pytest.approx(2.0)

    def test_both_zero_is_zero_percent_not_a_crash(self):
        assert pct_diff(0.0, 0.0) == 0.0

    def test_first_value_zero_and_second_nonzero_is_infinity_not_a_zerodivisionerror(self):
        assert pct_diff(0.0, 5.0) == float("inf")
