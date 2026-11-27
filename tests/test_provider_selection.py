import unittest
from unittest.mock import patch

import market_data


def snapshot(provider, score, valid=True):
    feeds = {
        name: {"ok": index < score, "data": ({"sample": 1} if index < score else [])}
        for index, name in enumerate((
            "mark_price_funding", "open_interest_current",
            "open_interest_history_5m", "taker_flow_5m",
            "order_book", "recent_agg_trades",
        ))
    }
    return {
        "exchange": provider,
        "feeds": feeds,
        "data_quality": {
            "core_timeframes_ok": valid,
            "five_minute_data_fresh": valid,
            "warnings": [],
        },
    }


class ProviderSelectionTests(unittest.TestCase):
    def test_best_derivatives_coverage_wins_over_binance_when_valid(self):
        with patch.object(market_data, "collect_binance", return_value=snapshot("Binance", 2)), \
             patch.object(market_data, "collect_bybit", return_value=snapshot("Bybit", 4)), \
             patch.object(market_data, "collect_bitget", return_value=snapshot("Bitget", 3)), \
             patch.object(market_data, "collect_okx", return_value=snapshot("OKX", 5)):
            result = market_data.collect()
        self.assertEqual(result["exchange"], "OKX")
        self.assertEqual(result["provider_selection"]["derivatives_feeds_available"], 5)
        self.assertEqual(result["data_quality"]["derivatives_confluence_status"], "PARTIAL")

    def test_binance_wins_tie(self):
        with patch.object(market_data, "collect_binance", return_value=snapshot("Binance", 4)), \
             patch.object(market_data, "collect_bybit", return_value=snapshot("Bybit", 4)), \
             patch.object(market_data, "collect_bitget", return_value=snapshot("Bitget", 2)), \
             patch.object(market_data, "collect_okx", return_value=snapshot("OKX", 1)):
            result = market_data.collect()
        self.assertEqual(result["exchange"], "Binance")

    def test_cross_venue_open_interest_does_not_count_as_primary_coverage(self):
        payload = snapshot("Bitget", 6)
        payload["feeds"]["open_interest_history_5m"]["venue"] = "OKX BTC-USDT-SWAP (cross-venue Futures context)"
        payload["feeds"]["open_interest_history_5m"]["note"] = "Historical OI is sourced from OKX perpetuals, not the primary Bitget execution venue."
        self.assertEqual(market_data.derivatives_feed_score(payload), 5)

    def test_empty_feed_does_not_count_as_available(self):
        payload = snapshot("Binance", 6)
        payload["feeds"]["order_book"]["data"] = {}
        self.assertEqual(market_data.derivatives_feed_score(payload), 5)

    def test_no_fresh_complete_futures_snapshot_fails_closed(self):
        with patch.object(market_data, "collect_binance", return_value=snapshot("Binance", 6, False)), \
             patch.object(market_data, "collect_bybit", return_value=snapshot("Bybit", 6, False)), \
             patch.object(market_data, "collect_bitget", return_value=snapshot("Bitget", 6, False)), \
             patch.object(market_data, "collect_okx", return_value=snapshot("OKX", 6, False)):
            result = market_data.collect()
        self.assertIsNone(result["provider_selection"]["selected_provider"])
        self.assertEqual(result["data_quality"]["status"], "DEGRADED")
        self.assertFalse(result["data_quality"]["core_timeframes_ok"])


if __name__ == "__main__":
    unittest.main()
