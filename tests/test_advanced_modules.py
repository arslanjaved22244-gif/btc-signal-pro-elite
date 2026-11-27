import importlib.util
import os
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "advanced_modules.py"
SPEC = importlib.util.spec_from_file_location("btc_advanced_modules", PATH)
ADV = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADV)


def make_candles(count=500):
    rows = []
    price = 50000.0
    for i in range(count):
        # Deterministic alternating trend with enough range for EMA/MACD.
        drift = 8.0 if (i // 35) % 2 == 0 else -6.0
        price += drift
        rows.append({
            "time": (i + 1) * 300000,
            "open": price - 2,
            "high": price + 15,
            "low": price - 15,
            "close": price,
            "volume": 10.0,
        })
    return rows


class AdvancedModulesTests(unittest.TestCase):
    def test_indicators_require_ema200_history(self):
        self.assertEqual(ADV.indicators(make_candles(200)), {"available": False})
        result = ADV.indicators(make_candles(250))
        self.assertTrue(result["available"])
        self.assertIn("ema200", result)
        self.assertIn("macd", result)
        self.assertIn("structure", result)
        self.assertIn("atr_pct", result)

    def test_backtest_requires_minimum_history(self):
        result = ADV.backtest(make_candles(299))
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")

    def test_backtest_reports_holdout_and_transaction_costs(self):
        result = ADV.backtest(make_candles(500))
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["test_candles"], 200)
        self.assertIn("final 20%", result["method"])
        self.assertGreater(result["round_trip_cost_pct"], 0)
        self.assertIn("funding", result["warning"])

    def test_risk_calculator_never_assumes_account_equity(self):
        result = ADV.risk_calc({"signal": "LONG", "risk_levels": {
            "entry_reference": 100, "stop_loss": 99
        }}, None)
        self.assertEqual(result["status"], "WAIT_OR_EQUITY_REQUIRED")
        self.assertEqual(result["leverage_cap"], 10)

    def test_risk_calculator_caps_leverage_and_calculates_loss_budget(self):
        result = ADV.risk_calc({"signal": "LONG", "risk_levels": {
            "entry_reference": 100, "stop_loss": 98
        }}, 1000)
        self.assertEqual(result["status"], "CALCULATED")
        self.assertEqual(result["leverage_cap"], 10)
        self.assertAlmostEqual(result["max_planned_loss_usdt"], 10)
        self.assertAlmostEqual(result["position_qty_btc"], 5.0)
        self.assertLessEqual(result["effective_leverage"], 10)


if __name__ == "__main__":
    unittest.main()
