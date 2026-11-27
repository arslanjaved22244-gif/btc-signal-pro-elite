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


    def test_precomputed_features_include_causal_market_context(self):
        candles = []
        price = 10000.0
        for i in range(260):
            drift = 1.0 if i % 3 else -0.4
            open_price = price
            close = price + drift
            candles.append({
                "time": i * 300000, "open": open_price,
                "high": max(open_price, close) + 2.0,
                "low": min(open_price, close) - 1.5,
                "close": close, "volume": 100.0 + i % 11
            })
            price = close
        prepared = AI.precompute_candle_features(candles)
        self.assertEqual(len(prepared[199]), 23)
        self.assertEqual(len(prepared[-1]), 23)
        self.assertTrue(all(value == value for value in prepared[-1]))
        # Changing a future candle must not alter an earlier feature vector.
        original = list(prepared[220])
        altered = [dict(item) for item in candles]
        altered[240]["high"] += 500.0
        altered[240]["volume"] *= 10.0
        changed = AI.precompute_candle_features(altered)
        self.assertEqual(original, changed[220])

    def test_ensemble_prediction_uses_same_declared_weights(self):
        class Stub:
            def __init__(self, values):
                self.values = values
        model = {
            "full_history": Stub([0.8, 0.1, 0.1]),
            "recent_regime": Stub([0.1, 0.8, 0.1]),
            "centroid": Stub([0.1, 0.1, 0.8]),
            "stumps": Stub([0.4, 0.3, 0.3])
        }
        original_predict = AI.predict
        original_centroid = AI.predict_centroid
        original_stumps = AI.predict_stumps
        try:
            AI.predict = lambda model, row: model.values
            AI.predict_centroid = lambda model, row: model.values
            AI.predict_stumps = lambda model, row: model.values
            result = AI.predict_ensemble(model, [0.0])
        finally:
            AI.predict = original_predict
            AI.predict_centroid = original_centroid
            AI.predict_stumps = original_stumps
        self.assertAlmostEqual(sum(result), 1.0, places=8)
        self.assertAlmostEqual(result[0], 0.35*0.8 + 0.20*0.1 + 0.20*0.1 + 0.25*0.4)
        self.assertAlmostEqual(result[1], 0.35*0.1 + 0.20*0.8 + 0.20*0.1 + 0.25*0.3)


if __name__ == "__main__":
    unittest.main()
