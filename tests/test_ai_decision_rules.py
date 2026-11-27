import importlib.util
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "ai_model.py" / "ai_model.py"
SPEC = importlib.util.spec_from_file_location("btc_ai_model_rules", PATH)
AI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AI)


class ActionableDecisionRuleTests(unittest.TestCase):
    def test_wait_probability_keeps_wait(self):
        self.assertEqual(AI.predict_actionable_class([0.35, 0.40, 0.25], 0.30), 1)

    def test_low_directional_confidence_is_wait(self):
        self.assertEqual(AI.predict_actionable_class([0.46, 0.20, 0.34], 0.30), 1)

    def test_small_top_two_margin_is_wait(self):
        self.assertEqual(AI.predict_actionable_class([0.47, 0.10, 0.43], 0.30), 1)

    def test_strong_short_is_actionable_candidate(self):
        self.assertEqual(AI.predict_actionable_class([0.62, 0.18, 0.20], 0.30), 0)

    def test_strong_long_is_actionable_candidate(self):
        self.assertEqual(AI.predict_actionable_class([0.18, 0.20, 0.62], 0.30), 2)


if __name__ == "__main__":
    unittest.main()
