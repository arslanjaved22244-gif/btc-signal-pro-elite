import importlib.util
import unittest
from unittest import mock
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


    def test_calibration_reports_all_threshold_candidates(self):
        rows = [[0.0] for _ in range(400)]
        labels = [1 for _ in rows]
        forward_returns = [0.01 for _ in rows]
        with mock.patch.object(AI, "train_ensemble", return_value=object()), \
             mock.patch.object(AI, "predict_ensemble", return_value=[0.05, 0.10, 0.85]):
            threshold, report = AI.calibrate_wait_threshold(rows, labels, forward_returns)

        self.assertGreaterEqual(threshold, 0.20)
        self.assertLessEqual(threshold, 0.60)
        self.assertEqual(report["calibration_samples"], 77)
        self.assertEqual(len(report["candidates"]), 9)
        self.assertGreater(report["eligible_candidate_count"], 0)
        self.assertEqual(report["selection_reason"], "best_positive_net_expectancy")
        self.assertTrue(all(item["eligible"] for item in report["candidates"]))
        self.assertTrue(all(item["trades"] > 0 for item in report["candidates"]))

    def test_unprofitable_calibration_forces_wait_and_reports_reason(self):
        rows = [[0.0] for _ in range(400)]
        labels = [1 for _ in rows]
        forward_returns = [-0.01 for _ in rows]
        with mock.patch.object(AI, "train_ensemble", return_value=object()), \
             mock.patch.object(AI, "predict_ensemble", return_value=[0.05, 0.10, 0.85]):
            threshold, report = AI.calibrate_wait_threshold(rows, labels, forward_returns)

        self.assertEqual(threshold, 1.01)
        self.assertEqual(report["eligible_candidate_count"], 0)
        self.assertEqual(report["selection_reason"], "no_candidate_met_profitability_rules")
        self.assertTrue(all(not item["eligible"] for item in report["candidates"]))

    def test_calibration_missing_returns_forces_wait(self):
        rows = [[0.0] for _ in range(400)]
        labels = [1 for _ in rows]
        threshold, report = AI.calibrate_wait_threshold(rows, labels)
        self.assertEqual(threshold, 1.01)
        self.assertEqual(report["selection_reason"], "missing_or_misaligned_forward_returns")

    def test_force_wait_sentinel_blocks_directional_predictions(self):
        probabilities = [0.80, 0.10, 0.10]
        self.assertEqual(AI.predict_with_wait_threshold(probabilities, 1.01), 1)
        self.assertEqual(AI.predict_actionable_class(probabilities, 1.01), 1)


if __name__ == "__main__":
    unittest.main()
