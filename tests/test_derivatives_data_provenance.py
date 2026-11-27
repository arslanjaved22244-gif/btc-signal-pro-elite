import importlib.util
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "market_data.py"
SPEC = importlib.util.spec_from_file_location("btc_market_data", PATH)
MARKET = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MARKET)


class DerivativesDataProvenanceTests(unittest.TestCase):
    def test_bybit_account_ratio_is_normalized_as_positioning(self):
        rows = MARKET.normalize_bybit_account_ratio([
            {"buyRatio": "0.60", "sellRatio": "0.40", "timestamp": "1700000000000"}
        ])
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["long_short_account_ratio"], 1.5)
        self.assertEqual(rows[0]["long_account_ratio"], 0.6)
        self.assertEqual(rows[0]["short_account_ratio"], 0.4)
        self.assertNotIn("buySellRatio", rows[0])

    def test_zero_short_ratio_does_not_divide_by_zero(self):
        rows = MARKET.normalize_bybit_account_ratio([
            {"buyRatio": "1", "sellRatio": "0", "timestamp": "1700000000000"}
        ])
        self.assertIsNone(rows[0]["long_short_account_ratio"])


if __name__ == "__main__":
    unittest.main()
