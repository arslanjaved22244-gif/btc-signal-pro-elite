import importlib.util
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ENGINE_PATH = Path(__file__).resolve().parents[1] / "signal_engine.py"
SPEC = importlib.util.spec_from_file_location("btc_signal_engine", ENGINE_PATH)
ENGINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ENGINE)


def make_market():
    now_ms = int(time.time() * 1000)
    durations = {"5m": 300000, "15m": 900000, "1h": 3600000, "4h": 14400000, "1d": 86400000}
    timeframes = {}
    for tf, duration in durations.items():
        rows = []
        for i in range(220):
            close = 10000.0 + i * 0.1
            rows.append({
                "time": now_ms - (220 - i) * duration - duration,
                "open": close - 0.05, "high": close + 0.5,
                "low": close - 0.5, "close": close,
                "volume": 100.0
            })
        timeframes[tf] = {"ok": True, "candles": rows, "age_seconds": 60}
    return {
        "market_type": "BYBIT_LINEAR_USDT_PERPETUAL",
        "exchange": "Bybit", "symbol": "BTCUSDT",
        "timeframes": timeframes, "feeds": {}, "derived": {},
        "data_quality": {
            "status": "HEALTHY", "five_minute_data_fresh": True,
            "core_timeframes_ok": True, "futures_only": True, "warnings": []
        }
    }


class FuturesSourceSafetyTests(unittest.TestCase):
    def run_with_source(self, source):
        market = make_market()
        model = {
            "data_source": source,
            "class_scores": {"LONG": 0.95, "SHORT": 0.02, "WAIT": 0.03},
            "model_version": "test"
        }
        payloads = {
            ENGINE.MARKET_FILE: market,
            ENGINE.MODEL_FILE: model,
            ENGINE.NEWS_FILE: {"available": True, "block": False}
        }
        with patch.object(ENGINE, "load", side_effect=lambda path: payloads[path]):
            return ENGINE.run()

    def test_spot_or_unknown_model_cannot_authorize_futures_direction(self):
        result = self.run_with_source("Kraken BTC/USD public OHLC candles")
        self.assertEqual(result["signal"], "WAIT")
        self.assertTrue(any("Directional entries blocked" in x for x in result["warnings"]))

    def test_verified_futures_model_can_confirm_direction_when_other_gates_pass(self):
        result = self.run_with_source("Bybit linear Futures public OHLC candles")
        self.assertEqual(result["signal"], "LONG")


if __name__ == "__main__":
    unittest.main()
