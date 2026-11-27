import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODEL_PATH = Path(__file__).resolve().parents[1] / "ai_model.py" / "ai_model.py"
SPEC = importlib.util.spec_from_file_location("btc_ai_model", MODEL_PATH)
MODEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODEL)


class AdaptiveLearningTests(unittest.TestCase):
    def run_weights(self, records, minimum_samples=30, market_regime=None):
        handle = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        path = Path(handle.name)
        try:
            json.dump({"version": 1, "predictions": records}, handle)
            handle.close()
            return MODEL.adaptive_component_weights(str(path), minimum_samples, market_regime)
        finally:
            path.unlink(missing_ok=True)

    def test_no_history_keeps_safe_baseline(self):
        weights, report = self.run_weights([])
        self.assertEqual(report["status"], "COLLECTING COMPONENT OUTCOMES")
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=8)
        self.assertEqual(weights["full_history_logistic"], 0.35)
        self.assertEqual(weights["decision_stumps"], 0.25)

    def test_adaptation_waits_for_minimum_samples(self):
        records = [{
            "model": "full_history_logistic", "status": "EVALUATED",
            "predicted_class": "LONG", "actual_class": "LONG", "correct": True
        }]
        weights, report = self.run_weights(records, minimum_samples=2)
        self.assertEqual(report["status"], "COLLECTING COMPONENT OUTCOMES")
        self.assertEqual(weights["full_history_logistic"], 0.35)

    def test_regime_specific_weights_require_regime_sample_minimum(self):
        records = []
        components = ("full_history_logistic", "recent_regime_logistic",
                      "nearest_centroid", "decision_stumps")
        for name in components:
            for index in range(30):
                records.append({
                    "model": name, "status": "EVALUATED",
                    "predicted_class": "LONG",
                    "actual_class": "LONG" if index < 24 else "SHORT",
                    "correct": index < 24, "market_regime": "BULL TREND"
                })
        weights, report = self.run_weights(records, minimum_samples=20, market_regime="BULL TREND")
        self.assertTrue(report["regime_specific_weights_active"])
        self.assertEqual(report["market_regime"], "BULL TREND")
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=8)

    def test_matured_lstm_feedback_filters_schema_and_future_examples(self):
        import time
        sequence = [[float(i), float(i + 1)] for i in range(24)]
        records = [
            {"model": "lstm_sequence_15m", "status": "EVALUATED",
             "actual_class": "LONG", "feature_mode": "MARKET_ONLY",
             "prediction_time": 100, "training_sequence": sequence},
            {"model": "lstm_sequence_15m", "status": "EVALUATED",
             "actual_class": "SHORT", "feature_mode": "OTHER",
             "prediction_time": 90, "training_sequence": sequence},
            {"model": "lstm_sequence_15m", "status": "PENDING",
             "actual_class": "WAIT", "feature_mode": "MARKET_ONLY",
             "prediction_time": 80, "training_sequence": sequence},
            {"model": "lstm_sequence_15m", "status": "EVALUATED",
             "actual_class": "SHORT", "feature_mode": "MARKET_ONLY",
             "prediction_time": 200, "training_sequence": sequence}
        ]
        handle = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        path = Path(handle.name)
        try:
            json.dump({"predictions": records}, handle)
            handle.close()
            examples, report = MODEL.load_matured_lstm_feedback(
                str(path), feature_mode="MARKET_ONLY", feature_count=2,
                before_time_ms=150
            )
            self.assertEqual(len(examples), 1)
            self.assertEqual(examples[0][1], 2)
            self.assertEqual(report["samples_added_to_training_fold"], 1)
            self.assertTrue(report["validation_and_test_sets_untouched"])
        finally:
            path.unlink(missing_ok=True)

    def test_weights_are_normalized_and_reward_reliable_models(self):
        records = []
        components = ("full_history_logistic", "recent_regime_logistic",
                      "nearest_centroid", "decision_stumps")
        for name in components:
            for index in range(30):
                correct = name == "full_history_logistic" or index < 10
                records.append({
                    "model": name, "status": "EVALUATED",
                    "predicted_class": "LONG",
                    "actual_class": "LONG" if correct else "SHORT",
                    "correct": correct
                })
        weights, report = self.run_weights(records)
        self.assertEqual(report["status"], "ADAPTIVE WEIGHTS ACTIVE")
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=8)
        self.assertGreater(weights["full_history_logistic"], weights["nearest_centroid"])
        self.assertTrue(all(0.0 < value < 1.0 for value in weights.values()))


    def test_cost_aware_move_threshold_covers_estimated_round_trip_cost(self):
        flat = [
            {"open": 10000.0, "high": 10000.0, "low": 10000.0,
             "close": 10000.0, "volume": 100.0}
            for _ in range(20)
        ]
        threshold = MODEL.adaptive_move_threshold(flat, 19, 3)
        self.assertGreaterEqual(threshold, 0.0018)

    def test_cost_aware_move_threshold_expands_with_volatility(self):
        quiet = [
            {"open": 10000.0, "high": 10005.0, "low": 9995.0,
             "close": 10000.0, "volume": 100.0}
            for _ in range(20)
        ]
        volatile = [
            {"open": 10000.0, "high": 10100.0, "low": 9900.0,
             "close": 10000.0, "volume": 100.0}
            for _ in range(20)
        ]
        quiet_threshold = MODEL.adaptive_move_threshold(quiet, 19, 3)
        volatile_threshold = MODEL.adaptive_move_threshold(volatile, 19, 3)
        self.assertGreater(volatile_threshold, quiet_threshold)


if __name__ == "__main__":
    unittest.main()
