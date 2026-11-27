import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

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

    def test_failed_validation_raw_direction_is_tracked_as_non_actionable_shadow(self):
        self.write_inputs(self.candles([100, 100, 100]), "WAIT")
        with open("ai_result.json", "w") as f:
            json.dump({
                "data_source": "OKX BTC-USDT-SWAP Futures public OHLC candles",
                "data_source_by_timeframe": {"5m": "OKX BTC-USDT-SWAP Futures"},
                "class_scores": {"LONG": 70, "SHORT": 10, "WAIT": 20},
                "validation_gate": {"passed": False},
                "model_version": "test"
            }, f)
        result = PAPER.run()
        shadow = result["shadow_forecasts"][0]
        self.assertEqual(shadow["side"], "LONG")
        self.assertEqual(shadow["status"], "PENDING")
        self.assertFalse(shadow["actionable"])
        self.assertFalse(shadow["validation_gate_passed"])

    def test_shadow_reports_directional_accuracy_separately_from_net_win_rate(self):
        self.write_inputs(self.candles([100, 100, 100]), "WAIT")
        with open("ai_result.json", "w") as f:
            json.dump({
                "data_source": "OKX BTC-USDT-SWAP Futures public OHLC candles",
                "data_source_by_timeframe": {"5m": "OKX BTC-USDT-SWAP Futures"},
                "class_scores": {"LONG": 70, "SHORT": 10, "WAIT": 20},
                "validation_gate": {"passed": False}
            }, f)
        PAPER.run()
        self.write_inputs(self.candles([100, 100, 100, 100.1, 100.1, 100.1]), "WAIT")
        with open("ai_result.json", "w") as f:
            json.dump({
                "data_source": "OKX BTC-USDT-SWAP Futures public OHLC candles",
                "data_source_by_timeframe": {"5m": "OKX BTC-USDT-SWAP Futures"},
                "class_scores": {"LONG": 70, "SHORT": 10, "WAIT": 20},
                "validation_gate": {"passed": False}
            }, f)
        result = PAPER.run()
        shadow = result["shadow_evaluation"]
        self.assertEqual(shadow["closed_forecasts"], 1)
        self.assertEqual(shadow["directional_accuracy_pct"], 100.0)
        self.assertEqual(shadow["net_win_rate_pct"], 0.0)
        self.assertLess(shadow["average_net_return_pct"], 0)

    def test_wait_is_recorded_as_abstention_not_trade(self):
        self.write_inputs(self.candles([100, 100, 100]), "WAIT")
        result = PAPER.run()
        self.assertEqual(result["wait_abstentions"], 1)
        self.assertEqual(result["closed_trades"], 0)


if __name__ == "__main__":
    unittest.main()
