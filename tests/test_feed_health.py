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


    def test_oi_freshness_uses_newest_timestamp_regardless_of_order(self):
        now_ms = int(self.now.timestamp() * 1000)
        self.market["feeds"]["open_interest_history_5m"]["data"] = [
            {"timestamp": now_ms - 60 * 60 * 1000, "sumOpenInterest": 90},
            {"timestamp": now_ms, "sumOpenInterest": 100}
        ]
        result = INTEL.feed_health(
            self.market, self.news, self.liquidation, self.comparison, now=self.now
        )
        states = {item["name"]: item["passed"] for item in result["items"]}
        self.assertTrue(states["open_interest_data"])

    def test_current_oi_snapshot_counts_but_is_not_called_history(self):
        self.market["feeds"]["open_interest_history_5m"] = {
            "ok": False, "error": "historical series unavailable"
        }
        self.market["feeds"]["open_interest_current"] = {
            "ok": True, "data": {"openInterest": "123"}
        }
        result = INTEL.feed_health(
            self.market, self.news, self.liquidation, self.comparison, now=self.now
        )
        oi = next(item for item in result["items"] if item["name"] == "open_interest_data")
        self.assertTrue(oi["passed"])
        self.assertIn("current OI snapshot", oi["detail"])

    def test_recent_derived_visible_depth_counts_when_raw_feed_flag_missing(self):
        self.market["feeds"]["order_book"] = {"ok": False}
        self.market["derived"].update({
            "visible_depth_bid_notional_usdt": 1000,
            "visible_depth_ask_notional_usdt": 900
        })
        result = INTEL.feed_health(
            self.market, self.news, self.liquidation, self.comparison, now=self.now
        )
        depth = next(item for item in result["items"] if item["name"] == "order_book_depth")
        self.assertTrue(depth["passed"])
        self.assertIn("derived top-20", depth["detail"])

    def test_stale_derived_visible_depth_does_not_count(self):
        self.market["generated_at"] = "2020-01-01T00:00:00+00:00"
        self.market["feeds"]["order_book"] = {"ok": False}
        self.market["derived"].update({
            "visible_depth_bid_notional_usdt": 1000,
            "visible_depth_ask_notional_usdt": 900
        })
        result = INTEL.feed_health(
            self.market, self.news, self.liquidation, self.comparison, now=self.now
        )
        depth = next(item for item in result["items"] if item["name"] == "order_book_depth")
        self.assertFalse(depth["passed"])

    def test_missing_timestamps_fail_closed(self):
        self.market["feeds"]["open_interest_history_5m"]["data"] = [
            {"sumOpenInterest": 100}
        ]
        result = INTEL.feed_health(
            self.market, self.news, self.liquidation, self.comparison, now=self.now
        )
        states = {item["name"]: item["passed"] for item in result["items"]}
        self.assertFalse(states["open_interest_data"])


    def test_quality_gate_requires_shadow_to_beat_its_majority_baseline(self):
        ai = {
            "validation_gate": {"passed": True},
            "walk_forward_metrics": {
                "accuracy": 65.0, "majority_class_baseline_pct": 60.0,
                "macro_f1": 35.0
            }
        }
        paper = {"shadow_evaluation": {
            "closed_forecasts": 30,
            "beats_majority_baseline": False,
            "validation_gate_passed_now": True
        }}
        metrics = {"backtest_5m": {
            "status": "COMPLETED", "total_candles": 500,
            "net_return_pct": 1.0
        }}
        result = INTEL.quality_gate(ai, paper, metrics)
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["shadow_beats_majority_baseline"])
        paper["shadow_evaluation"]["beats_majority_baseline"] = True
        result = INTEL.quality_gate(ai, paper, metrics)
        self.assertTrue(result["passed"])


if __name__ == "__main__":
    unittest.main()
