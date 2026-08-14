"""End-to-end tests for CSV import through the actual HTTP API.

The most important test in this file is
`test_csv_derived_request_matches_manual_entry`: it builds a
BearPutSpreadRequest from CSV-imported contracts exactly the way the
frontend does (see CsvImportWorkflow.tsx), posts it to the SAME
/api/bear-put-spread endpoint the manual-entry form uses, and checks
the response is identical to posting the graduation-example payload
directly. That is the proof that CSV import does not duplicate any
financial formula -- it only produces inputs for the existing engine.

Also verifies the original spec's worked-example numbers still appear
exactly -- as the Execution Reality Check (conservative) figures,
since Mid Debit is this app's primary convention (see the dual-debit
work in git history / README).
"""

import io
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _mcl_graduation_csv(dte: int = 30) -> bytes:
    expiration = (date.today() + timedelta(days=dte)).strftime("%m/%d/%Y")
    header = "Symbol,Underlying Price,Exp Date,Strike,Type,Bid,Ask,Last,Volume,Open Int,Impl Vol,Delta,Gamma,Theta,Vega"
    long_row = f"MCL,82.00,{expiration},85,PUT,5.00,5.10,5.05,120,340,44.00%,-0.58,0.05,-0.03,0.12"
    short_row = f"MCL,82.00,{expiration},77,PUT,0.71,0.85,0.78,95,210,42.00%,-0.29,0.04,-0.02,0.08"
    third_row = f"MCL,82.00,{expiration},80,PUT,2.10,2.20,2.15,60,180,43.00%,-0.42,0.06,-0.04,0.10"
    return "\n".join([header, long_row, short_row, third_row]).encode("utf-8")


class TestCsvImportEndpoint:
    def test_valid_csv_upload_returns_200_with_expected_shape(self):
        resp = client.post(
            "/api/csv-import",
            files={"file": ("chain.csv", io.BytesIO(_mcl_graduation_csv()), "text/csv")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported_rows"] == 3
        assert data["symbols"] == ["MCL"]
        assert len(data["expirations_by_symbol"]["MCL"]) == 1
        strikes = sorted(c["strike"] for c in data["contracts"])
        assert strikes == [77, 80, 85]

    def test_non_csv_extension_rejected(self):
        resp = client.post(
            "/api/csv-import",
            files={"file": ("chain.txt", io.BytesIO(b"not,a,csv"), "text/plain")},
        )
        assert resp.status_code == 422

    def test_empty_file_rejected(self):
        resp = client.post(
            "/api/csv-import",
            files={"file": ("chain.csv", io.BytesIO(b""), "text/csv")},
        )
        assert resp.status_code == 422

    def test_missing_required_column_rejected_with_clear_message(self):
        bad_csv = b"Symbol,Strike,Bid,Ask\nMCL,85,5.00,5.10\n"
        resp = client.post(
            "/api/csv-import",
            files={"file": ("chain.csv", io.BytesIO(bad_csv), "text/csv")},
        )
        assert resp.status_code == 422
        assert "Missing required column" in resp.json()["detail"]

    def test_file_with_only_invalid_rows_rejected(self):
        # Every row fails validation (negative bid) -> 0 importable rows.
        bad_csv = (
            "Symbol,Underlying Price,Exp Date,Strike,Type,Bid,Ask,Delta,Impl Vol\n"
            "MCL,82.00,09/18/2026,85,PUT,-5.00,5.10,-0.58,44.00%\n"
        ).encode("utf-8")
        resp = client.post(
            "/api/csv-import",
            files={"file": ("chain.csv", io.BytesIO(bad_csv), "text/csv")},
        )
        assert resp.status_code == 422


class TestCsvDerivedAnalysisMatchesManualEntry:
    def _import_and_select_spread(self) -> dict:
        resp = client.post(
            "/api/csv-import",
            files={"file": ("chain.csv", io.BytesIO(_mcl_graduation_csv()), "text/csv")},
        )
        assert resp.status_code == 200
        contracts = resp.json()["contracts"]
        long_contract = next(c for c in contracts if c["strike"] == 85)
        short_contract = next(c for c in contracts if c["strike"] == 77)
        return long_contract, short_contract

    def _build_request_from_contracts(self, long_c: dict, short_c: dict) -> dict:
        # Mirrors exactly what CsvImportWorkflow.tsx does when the user
        # clicks "Analyze Spread": map two NormalizedOption contracts
        # into a normal BearPutSpreadRequest. No math happens here.
        return {
            "underlying": {
                "symbol": long_c["symbol"],
                "price": long_c["underlying_price"],
                "dte": long_c["dte"],
            },
            "long_put": {
                "strike": long_c["strike"],
                "bid": long_c["bid"],
                "ask": long_c["ask"],
                "delta": long_c["delta"],
                "iv": long_c["implied_volatility"],
            },
            "short_put": {
                "strike": short_c["strike"],
                "bid": short_c["bid"],
                "ask": short_c["ask"],
                "delta": short_c["delta"],
                "iv": short_c["implied_volatility"],
            },
        }

    def test_csv_derived_request_matches_manual_entry(self):
        long_c, short_c = self._import_and_select_spread()
        csv_derived_request = self._build_request_from_contracts(long_c, short_c)

        manual_payload = {
            "underlying": {"symbol": "MCL", "price": 82.00, "dte": 30},
            "long_put": {"strike": 85, "bid": 5.00, "ask": 5.10, "delta": -0.58, "iv": 0.44},
            "short_put": {"strike": 77, "bid": 0.71, "ask": 0.85, "delta": -0.29, "iv": 0.42},
        }

        csv_resp = client.post("/api/bear-put-spread", json=csv_derived_request)
        manual_resp = client.post("/api/bear-put-spread", json=manual_payload)

        assert csv_resp.status_code == 200
        assert manual_resp.status_code == 200
        # The two responses must be byte-for-byte identical: same
        # engine, same inputs, so there is nothing left that could
        # differ.
        assert csv_resp.json() == manual_resp.json()

    def test_graduation_numbers_primary_mid_debit(self):
        # Current app behavior: Mid Debit is primary.
        long_c, short_c = self._import_and_select_spread()
        request = self._build_request_from_contracts(long_c, short_c)
        resp = client.post("/api/bear-put-spread", json=request)
        data = resp.json()

        assert round(data["debit"]["debit_per_contract"], 2) == 427.0
        assert round(data["risk_reward"]["max_loss_per_contract"], 2) == 427.0
        assert round(data["risk_reward"]["max_profit_per_contract"], 2) == 373.0
        assert round(data["risk_reward"]["breakeven"], 2) == 80.73
        assert round(data["delta"]["spread_delta"], 2) == -0.29
        assert round(data["volatility"]["average_iv"], 2) == 0.43
        assert round(data["volatility"]["expected_move"], 2) == pytest.approx(10.10, abs=0.02)

    def test_graduation_numbers_execution_reality_check_match_original_spec(self):
        # The original spec's worked-example numbers ($4.39 debit,
        # $439 max loss, $361 max profit, $80.61 breakeven, ~44.5%
        # probability) are still produced exactly -- just as the
        # Conservative Entry Debit / Execution Reality Check figures,
        # since that is what Ask-Bid computes.
        long_c, short_c = self._import_and_select_spread()
        request = self._build_request_from_contracts(long_c, short_c)
        resp = client.post("/api/bear-put-spread", json=request)
        data = resp.json()

        exec_check = data["execution_check"]
        assert round(exec_check["conservative_debit_per_contract"], 2) == 439.0
        assert round(exec_check["conservative_max_loss_per_contract"], 2) == 439.0
        assert round(exec_check["conservative_max_profit_per_contract"], 2) == 361.0
        assert round(exec_check["conservative_breakeven"], 2) == 80.61


class TestLongShortStrikeValidationViaCsvFlow:
    def test_reversed_strikes_rejected_by_existing_validator(self):
        # Select the strikes backwards (long < short) -- the existing
        # BearPutSpreadRequest validator (models/bear_put_spread.py)
        # must catch this; CSV import does not need its own copy.
        resp = client.post(
            "/api/csv-import",
            files={"file": ("chain.csv", io.BytesIO(_mcl_graduation_csv()), "text/csv")},
        )
        contracts = resp.json()["contracts"]
        low_strike = next(c for c in contracts if c["strike"] == 77)
        high_strike = next(c for c in contracts if c["strike"] == 85)

        bad_request = {
            "underlying": {"symbol": "MCL", "price": 82.0, "dte": low_strike["dte"]},
            "long_put": {  # long put strike set LOWER than short -- invalid
                "strike": low_strike["strike"],
                "bid": low_strike["bid"],
                "ask": low_strike["ask"],
                "delta": low_strike["delta"],
                "iv": low_strike["implied_volatility"],
            },
            "short_put": {
                "strike": high_strike["strike"],
                "bid": high_strike["bid"],
                "ask": high_strike["ask"],
                "delta": high_strike["delta"],
                "iv": high_strike["implied_volatility"],
            },
        }
        analyze_resp = client.post("/api/bear-put-spread", json=bad_request)
        assert analyze_resp.status_code == 422
