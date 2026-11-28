import importlib.util
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "ai_model.py" / "ai_model.py"
SPEC = importlib.util.spec_from_file_location("btc_ai_model_validation_gate", PATH)
AI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AI)


def passing_validation():
    return {
        "accuracy": 65.0,
        "macro_f1": 42.0,
        "majority_class_baseline_pct": 60.0,
        "out_of_sample_samples": 900,
        "cost_aware_backtest": {
            "trades": 100,
            "average_net_trade_return_pct": 0.08,
            "estimated_net_win_rate_pct": 56.0,
            "directional_performance": {
                "SHORT": {
                    "trades": 50,
                    "average_net_trade_return_pct": 0.07,
                    "net_win_rate_pct": 55.0,
                },
                "LONG": {
                    "trades": 50,
                    "average_net_trade_return_pct": 0.09,
                    "net_win_rate_pct": 57.0,
                },
            },
        },
    }


class ValidationGateRegressionTests(unittest.TestCase):
    def test_all_quality_and_profitability_checks_are_required(self):
        passed, report = AI.evaluate_validation_gate(passing_validation(), True)
        self.assertTrue(passed)
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(report["decision"], "directional signals allowed")

    def test_negative_net_return_blocks_directional_signals(self):
        validation = passing_validation()
        validation["cost_aware_backtest"]["average_net_trade_return_pct"] = -0.01
        passed, report = AI.evaluate_validation_gate(validation, True)
        self.assertFalse(passed)
        self.assertFalse(report["checks"]["positive_average_net_return_passed"])
        self.assertEqual(report["decision"], "WAIT only — profitability evidence insufficient")

    def test_missing_or_unprofitable_long_side_blocks_directional_signals(self):
        validation = passing_validation()
        validation["cost_aware_backtest"]["directional_performance"]["LONG"][
            "average_net_trade_return_pct"
        ] = -0.02
        passed, report = AI.evaluate_validation_gate(validation, True)
        self.assertFalse(passed)
        self.assertFalse(report["checks"]["long_direction_passed"])

    def test_missing_futures_source_blocks_directional_signals(self):
        passed, report = AI.evaluate_validation_gate(passing_validation(), False)
        self.assertFalse(passed)
        self.assertFalse(report["checks"]["futures_source_passed"])


if __name__ == "__main__":
    unittest.main()
