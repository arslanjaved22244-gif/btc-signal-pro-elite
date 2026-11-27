import importlib.util
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "market_data.py"
SPEC = importlib.util.spec_from_file_location("btc_market_data", PATH)
MARKET = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MARKET)


class OrderBookImbalanceTests(unittest.TestCase):
    def test_equal_quote_notional_is_zero(self):
        bids = [["100", "2"], ["99", "1"]]
        asks = [["100", "2"], ["101", "1"]]
        self.assertEqual(MARKET.normalized_order_book_imbalance(bids, asks), 0.0)

    def test_bid_heavy_book_is_positive(self):
        bids = [["100", "4"]]
        asks = [["100", "2"]]
        self.assertAlmostEqual(MARKET.normalized_order_book_imbalance(bids, asks), 1 / 3, places=6)

    def test_ask_heavy_book_is_negative(self):
        bids = [["100", "2"]]
        asks = [["100", "4"]]
        self.assertAlmostEqual(MARKET.normalized_order_book_imbalance(bids, asks), -1 / 3, places=6)

    def test_depth_is_limited_to_requested_levels(self):
        bids = [["100", "1"], ["100", "100"]]
        asks = [["100", "1"], ["100", "0"]]
        self.assertEqual(MARKET.normalized_order_book_imbalance(bids, asks, depth=1), 0.0)

    def test_empty_book_returns_none(self):
        self.assertIsNone(MARKET.normalized_order_book_imbalance([], []))


if __name__ == "__main__":
    unittest.main()
