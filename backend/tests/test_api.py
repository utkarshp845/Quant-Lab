"""End-to-end tests hitting the FastAPI app through the ASGI test client.

These confirm the API wiring (request -> validation -> calculations ->
response) works together, on top of the unit tests that already cover
each calculation in isolation.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

GRADUATION_PAYLOAD = {
    "underlying": {"symbol": "XYZ", "price": 82.00, "dte": 30},
    "long_put": {"strike": 85, "bid": 5.00, "ask": 5.10, "delta": -0.58, "iv": 0.44},
    "short_put": {"strike": 77, "bid": 0.71, "ask": 0.85, "delta": -0.29, "iv": 0.42},
}


class TestHealthCheck:
    def test_health(self):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestBearPutSpreadEndpoint:
    def test_graduation_example_full_response(self):
        resp = client.post("/api/bear-put-spread", json=GRADUATION_PAYLOAD)
        assert resp.status_code == 200
        data = resp.json()

        # Primary (mid-debit based) figures -- drive Risk/Reward, Probability, etc.
        assert round(data["debit"]["debit_per_share"], 2) == 4.27
        assert round(data["debit"]["debit_per_contract"], 2) == 427.0
        assert round(data["risk_reward"]["max_loss_per_contract"], 2) == 427.0
        assert round(data["risk_reward"]["max_profit_per_contract"], 2) == 373.0
        assert round(data["risk_reward"]["breakeven"], 2) == 80.73
        assert round(data["delta"]["spread_delta"], 2) == -0.29
        assert round(data["volatility"]["average_iv"], 2) == 0.43
        assert round(data["volatility"]["expected_move"], 2) == 10.11
        assert round(data["probability"]["z_score"], 3) == -0.126
        assert round(data["probability"]["probability_below_breakeven"], 3) == 0.450

        # Secondary (conservative, ask/bid-based) figures -- the
        # Execution Reality Check. This is the original spec's
        # worked-example debit of $4.39, still exact, just relabeled.
        exec_check = data["execution_check"]
        assert round(exec_check["conservative_debit_per_share"], 2) == 4.39
        assert round(exec_check["conservative_debit_per_contract"], 2) == 439.0
        assert round(exec_check["conservative_max_loss_per_contract"], 2) == 439.0
        assert round(exec_check["conservative_max_profit_per_contract"], 2) == 361.0
        assert round(exec_check["conservative_breakeven"], 2) == 80.61
        assert round(exec_check["slippage_cost_per_contract"], 2) == 12.0

        # Payoff table should include the key reference prices.
        labels = {row["label"] for row in data["payoff_table"] if row["label"]}
        assert "Short Put Strike" in labels
        assert "Long Put Strike" in labels
        assert "Breakeven" in labels
        assert "Current Price" in labels

        # Chart should have exactly 4 breakpoints (piecewise-linear shape).
        assert len(data["payoff_chart_points"]) == 4

        summary = data["summary"]
        assert summary["symbol"] == "XYZ"
        assert round(summary["breakeven"], 2) == 80.73
        assert round(summary["debit_per_contract"], 2) == 427.0
        assert round(summary["conservative_debit_per_contract"], 2) == 439.0

    def test_invalid_strike_ordering_returns_422(self):
        bad_payload = {
            **GRADUATION_PAYLOAD,
            "long_put": {**GRADUATION_PAYLOAD["long_put"], "strike": 70},
        }
        resp = client.post("/api/bear-put-spread", json=bad_payload)
        assert resp.status_code == 422

    def test_negative_price_returns_422(self):
        bad_payload = {
            **GRADUATION_PAYLOAD,
            "underlying": {**GRADUATION_PAYLOAD["underlying"], "price": -1},
        }
        resp = client.post("/api/bear-put-spread", json=bad_payload)
        assert resp.status_code == 422

    def test_missing_field_returns_422(self):
        bad_payload = {"underlying": GRADUATION_PAYLOAD["underlying"]}  # missing legs
        resp = client.post("/api/bear-put-spread", json=bad_payload)
        assert resp.status_code == 422

    def test_zero_dte_returns_422_not_500(self):
        # DTE=0 is a valid *input* (see UnderlyingInput), but it makes
        # the expected move zero, which makes the z-score undefined.
        # This must surface as a clean 422, not an unhandled 500.
        bad_payload = {
            **GRADUATION_PAYLOAD,
            "underlying": {**GRADUATION_PAYLOAD["underlying"], "dte": 0},
        }
        resp = client.post("/api/bear-put-spread", json=bad_payload)
        assert resp.status_code == 422
        assert "z-score" in resp.json()["detail"]


class TestPayoffAtPriceEndpoint:
    def test_payoff_at_breakeven_is_zero(self):
        # Breakeven is now mid-debit based: 80.73, not 80.61.
        payload = {**GRADUATION_PAYLOAD, "expiration_price": 80.73}
        resp = client.post("/api/bear-put-spread/payoff-at-price", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert abs(data["pl_per_share"]) < 0.01

    def test_payoff_above_long_strike_is_max_loss(self):
        payload = {**GRADUATION_PAYLOAD, "expiration_price": 90}
        resp = client.post("/api/bear-put-spread/payoff-at-price", json=payload)
        data = resp.json()
        assert round(data["pl_per_contract"], 2) == -427.0

    def test_payoff_below_short_strike_is_max_profit(self):
        payload = {**GRADUATION_PAYLOAD, "expiration_price": 70}
        resp = client.post("/api/bear-put-spread/payoff-at-price", json=payload)
        data = resp.json()
        assert round(data["pl_per_contract"], 2) == 373.0


class TestMonteCarloEndpoint:
    def test_default_simulation_runs_and_returns_full_shape(self):
        payload = {**GRADUATION_PAYLOAD, "num_simulations": 5000, "seed": 42}
        resp = client.post("/api/bear-put-spread/monte-carlo", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["num_simulations"] == 5000
        assert 0.0 <= data["probability_of_profit"] <= 1.0
        assert len(data["sample_paths"]) == 10
        assert len(data["histogram"]) > 0
        # Closed-form EV should be included for comparison and should
        # be in the same ballpark as the graduation-example figure
        # (mid-debit based, matching the main analysis endpoint).
        assert round(data["closed_form_expected_value_per_contract"], 2) == pytest.approx(-57.77, abs=1.0)

    def test_same_seed_is_reproducible_through_the_api(self):
        payload = {**GRADUATION_PAYLOAD, "num_simulations": 2000, "seed": 99}
        resp1 = client.post("/api/bear-put-spread/monte-carlo", json=payload)
        resp2 = client.post("/api/bear-put-spread/monte-carlo", json=payload)
        assert resp1.json()["expected_value_per_contract"] == resp2.json()["expected_value_per_contract"]

    def test_too_few_simulations_rejected(self):
        payload = {**GRADUATION_PAYLOAD, "num_simulations": 10}
        resp = client.post("/api/bear-put-spread/monte-carlo", json=payload)
        assert resp.status_code == 422

    def test_zero_dte_returns_422_not_500(self):
        payload = {
            **GRADUATION_PAYLOAD,
            "underlying": {**GRADUATION_PAYLOAD["underlying"], "dte": 0},
            "num_simulations": 1000,
        }
        resp = client.post("/api/bear-put-spread/monte-carlo", json=payload)
        assert resp.status_code == 422

    def test_invalid_strike_ordering_rejected(self):
        payload = {
            **GRADUATION_PAYLOAD,
            "long_put": {**GRADUATION_PAYLOAD["long_put"], "strike": 70},
            "num_simulations": 1000,
        }
        resp = client.post("/api/bear-put-spread/monte-carlo", json=payload)
        assert resp.status_code == 422
