import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "institutional_intelligence.py"
SPEC = importlib.util.spec_from_file_location("btc_institutional_intelligence", PATH)
INTEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INTEL)


class FeedHealthTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        stamp = self.now.isoformat()
        now_ms = int(self.now.timestamp() * 1000)
        self.market = {
            "generated_at": stamp,
            "timeframes": {
                tf: {"ok": True, "age_seconds": 60}
                for tf in ("5m", "15m", "1h", "4h", "1d")
            },
            "feeds": {
                "mark_price_funding": {"ok": True, "data": {}},
                "open_interest_history_5m": {"ok": True, "data": [
                    {"timestamp": now_ms, "sumOpenInterest": 100}
                ]},
                "taker_flow_5m": {"ok": True, "data": [{"timestamp": now_ms}]},
                "order_book": {"ok": True, "data": {}}
            },
            "derived": {"cvd_base": 0.5, "trade_count": 100}
        }
        self.news = {
            "available": True, "generated_at": stamp,
            "sources": ["Google News RSS", "Trump's Truth independent archive (unofficial)"]
        }
        self.liquidation = {
            "continuous": True, "connected": True, "last_message_at": stamp
        }
        self.comparison = {
            "generated_at": stamp,
            "venues": {
                "Binance": {"available": True},
                "OKX": {"available": True}
            }
        }

    def test_all_eleven_sources_pass_when_fresh(self):
        result = INTEL.feed_health(
            self.market, self.news, self.liquidation, self.comparison, now=self.now
        )
        self.assertEqual(result["total"], 11)
        self.assertEqual(result["passed"], 11)
        self.assertEqual(result["status"], "ALL_AVAILABLE")

    def test_stale_news_and_liquidations_are_not_reported_as_live(self):
        self.news["generated_at"] = "2020-01-01T00:00:00+00:00"
        self.liquidation["last_message_at"] = "2020-01-01T00:00:00+00:00"
        result = INTEL.feed_health(
            self.market, self.news, self.liquidation, self.comparison, now=self.now
        )
        states = {item["name"]: item["passed"] for item in result["items"]}
        self.assertFalse(states["fresh_news_scan"])
        self.assertFalse(states["continuous_liquidation_stream"])
        self.assertEqual(result["status"], "PARTIAL")

    def test_missing_timestamps_fail_closed(self):
        self.market["feeds"]["open_interest_history_5m"]["data"] = [
            {"sumOpenInterest": 100}
        ]
        result = INTEL.feed_health(
            self.market, self.news, self.liquidation, self.comparison, now=self.now
        )
        states = {item["name"]: item["passed"] for item in result["items"]}
        self.assertFalse(states["open_interest_history"])


if __name__ == "__main__":
    unittest.main()
