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

    def test_wait_is_recorded_as_abstention_not_trade(self):
        self.write_inputs(self.candles([100, 100, 100]), "WAIT")
        result = PAPER.run()
        self.assertEqual(result["wait_abstentions"], 1)
        self.assertEqual(result["closed_trades"], 0)


if __name__ == "__main__":
    unittest.main()
