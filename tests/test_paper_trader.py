import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


def datetime_now():
    return datetime.now(timezone.utc).isoformat()

PATH = Path(__file__).resolve().parents[1] / "paper_trader.py"
SPEC = importlib.util.spec_from_file_location("paper_trader", PATH)
PAPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PAPER)


class PaperTraderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.getcwd()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.old)
        self.tmp.cleanup()

    def write_inputs(self, candles, side="LONG"):
        market = {
            "exchange": "OKX",
            "data_quality": {"futures_only": True, "core_timeframes_ok": True},
            "timeframes": {"5m": {"candles": candles}}
        }
        with open("market_data.json", "w") as f:
            json.dump(market, f)
        with open("signal_result.json", "w") as f:
            json.dump({"signal": side, "reason": "test"}, f)

    def candles(self, closes):
        return [{"time": (i + 1) * 300000, "open": p, "high": p + 1,
                 "low": p - 1, "close": p, "volume": 10}
                for i, p in enumerate(closes)]

    def test_long_paper_trade_resolves_after_three_closed_bars_net_of_costs(self):
        self.write_inputs(self.candles([100, 100, 100]), "LONG")
        PAPER.run()
        self.write_inputs(self.candles([100, 100, 100, 101, 102, 103]), "WAIT")
        result = PAPER.run()
        self.assertEqual(result["closed_trades"], 1)
        self.assertEqual(result["wins"], 1)
        self.assertAlmostEqual(result["records"][0]["net_return_pct"], 2.86, places=2)

    def write_ai(self, generated_at):
        with open("market_data.json", encoding="utf-8") as market_file:
            market = json.load(market_file)
        latest = market["timeframes"]["5m"]["candles"][-1]
        with open("ai_result.json", "w") as f:
            json.dump({
                "generated_at": generated_at,
                "symbol": "BTC/USDT",
                "last_price": latest["close"],
                "last_candle_time": latest["time"],
                "data_source": "OKX BTC-USDT-SWAP Futures public OHLC candles",
                "data_source_by_timeframe": {"5m": "OKX BTC-USDT-SWAP Futures"},
                "class_scores": {"LONG": 70, "SHORT": 10, "WAIT": 20},
                "validation_gate": {"passed": False},
                "model_version": "test"
            }, f)

    def test_failed_validation_raw_direction_is_tracked_as_non_actionable_shadow(self):
        self.write_inputs(self.candles([100, 100, 100]), "WAIT")
        self.write_ai(datetime_now())
        result = PAPER.run()
        shadow = result["shadow_forecasts"][0]
        self.assertEqual(shadow["side"], "LONG")
        self.assertEqual(shadow["status"], "PENDING")
        self.assertFalse(shadow["actionable"])
        self.assertFalse(shadow["validation_gate_passed"])

    def test_shadow_reports_directional_accuracy_separately_from_net_win_rate(self):
        self.write_inputs(self.candles([100, 100, 100]), "WAIT")
        self.write_ai(datetime_now())
        PAPER.run()
        self.write_inputs(self.candles([100, 100, 100, 100.1, 100.1, 100.1]), "WAIT")
        self.write_ai(datetime_now())
        result = PAPER.run()
        shadow = result["shadow_evaluation"]
        self.assertEqual(shadow["closed_forecasts"], 1)
        self.assertEqual(shadow["directional_accuracy_pct"], 100.0)
        self.assertEqual(shadow["net_win_rate_pct"], 0.0)
        self.assertLess(shadow["average_net_return_pct"], 0)

    def test_same_ai_generation_is_not_counted_twice(self):
        self.write_inputs(self.candles([100, 100, 100]), "WAIT")
        stamp = datetime_now()
        self.write_ai(stamp)
        first = PAPER.run()
        self.write_inputs(self.candles([100, 100, 100, 100, 100, 100]), "WAIT")
        self.write_ai(stamp)
        second = PAPER.run()
        self.assertEqual(len(first["shadow_forecasts"]), 1)
        self.assertEqual(len(second["shadow_forecasts"]), 1)

    def test_stale_ai_output_is_not_recorded(self):
        self.write_inputs(self.candles([100, 100, 100]), "WAIT")
        self.write_ai("2000-01-01T00:00:00+00:00")
        result = PAPER.run()
        self.assertEqual(len(result["shadow_forecasts"]), 0)
        self.assertFalse(result["shadow_evaluation"]["latest_model_fresh"])

    def test_cross_venue_futures_are_accepted_only_within_basis_and_time_tolerance(self):
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        ai = {
            "data_source": "OKX BTC-USDT-SWAP Futures public OHLC candles",
            "data_source_by_timeframe": {"5m": "OKX BTC-USDT-SWAP Futures"},
            "symbol": "BTC/USDT", "last_price": 60000,
            "last_candle_time": now_ms
        }
        market = {
            "exchange": "Bitget", "market_type": "BITGET_USDT_FUTURES",
            "symbol": "BTCUSDT", "data_quality": {"futures_only": True}
        }
        aligned = PAPER.assess_venue_alignment(ai, market, 60050, now_ms + 300000)
        self.assertTrue(aligned["compatible"])
        self.assertEqual(aligned["type"], "CROSS_VENUE_FUTURES")

    def test_cross_venue_shadow_scoring_blocks_large_basis_or_stale_candles(self):
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        ai = {
            "data_source": "OKX BTC-USDT-SWAP Futures public OHLC candles",
            "symbol": "BTC/USDT", "last_price": 60000,
            "last_candle_time": now_ms - 30 * 60 * 1000
        }
        market = {
            "exchange": "Bitget", "market_type": "BITGET_USDT_FUTURES",
            "symbol": "BTCUSDT", "data_quality": {"futures_only": True}
        }
        divergent = PAPER.assess_venue_alignment(ai, market, 60300, now_ms)
        stale = PAPER.assess_venue_alignment(ai, market, 60010, now_ms)
        self.assertFalse(divergent["compatible"])
        self.assertFalse(stale["compatible"])

    def test_same_venue_shadow_scoring_still_requires_fresh_price_and_time(self):
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        ai = {
            "data_source": "OKX BTC-USDT-SWAP Futures public OHLC candles",
            "data_source_by_timeframe": {"5m": "OKX BTC-USDT-SWAP Futures"},
            "symbol": "BTC/USDT", "last_price": 60000,
            "last_candle_time": now_ms - 16 * 60 * 1000
        }
        market = {
            "exchange": "OKX", "market_type": "OKX_SWAP",
            "symbol": "BTCUSDT", "data_quality": {"futures_only": True}
        }
        result = PAPER.assess_venue_alignment(ai, market, 60000, now_ms)
        self.assertFalse(result["compatible"])
        self.assertEqual(result["type"], "BLOCKED")

    def test_spot_model_cannot_be_shadow_scored_against_futures(self):
        ai = {"data_source": "Binance BTCUSDT Spot public candles", "symbol": "BTC/USDT"}
        market = {"exchange": "Bitget", "data_quality": {"futures_only": True}}
        result = PAPER.assess_venue_alignment(ai, market, 60000, 1700000000000)
        self.assertFalse(result["compatible"])

    def test_wait_is_recorded_as_abstention_not_trade(self):
        self.write_inputs(self.candles([100, 100, 100]), "WAIT")
        result = PAPER.run()
        self.assertEqual(result["wait_abstentions"], 1)
        self.assertEqual(result["closed_trades"], 0)


    def test_shadow_wait_forecast_is_evaluated_as_a_class_not_trade(self):
        self.write_inputs(self.candles([100, 100, 100]), "WAIT")
        self.write_ai(datetime_now())
        with open("ai_result.json", "r", encoding="utf-8") as f:
            ai = json.load(f)
        ai["class_scores"] = {"LONG": 10, "SHORT": 10, "WAIT": 80}
        with open("ai_result.json", "w", encoding="utf-8") as f:
            json.dump(ai, f)
        result = PAPER.run()
        self.assertEqual(result["shadow_forecasts"][0]["side"], "WAIT")
        self.write_inputs(self.candles([100, 100, 100, 100, 100, 100]), "WAIT")
        with open("ai_result.json", "w", encoding="utf-8") as f:
            json.dump(ai, f)
        result = PAPER.run()
        shadow = result["shadow_forecasts"][0]
        self.assertEqual(shadow["classification_status"], "EVALUATED")
        self.assertEqual(shadow["actual_class"], "WAIT")
        self.assertTrue(shadow["classification_correct"])
        self.assertEqual(result["shadow_evaluation"]["closed_forecasts"], 1)
        self.assertEqual(result["shadow_evaluation"]["classification_accuracy_pct"], 100.0)
        self.assertEqual(result["shadow_evaluation"]["net_win_rate_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
