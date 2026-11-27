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
    def run_weights(self, records, minimum_samples=30):
        handle = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        path = Path(handle.name)
        try:
            json.dump({"version": 1, "predictions": records}, handle)
            handle.close()
            return MODEL.adaptive_component_weights(str(path), minimum_samples)
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


if __name__ == "__main__":
    unittest.main()
