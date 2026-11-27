import json
import tempfile
import unittest
from pathlib import Path

from runner_safety import publish_wait_override


class RunnerSafetyTests(unittest.TestCase):
    def test_market_failure_replaces_directional_signal_with_wait(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "signal_result.json"
            path.write_text(json.dumps({
                "signal": "LONG", "confidence_score": 91,
                "generated_at": "2026-01-01T00:00:00+00:00",
                "risk_levels": {"entry_reference": 100, "stop_loss": 95,
                                "take_profit_1": 110, "take_profit_2": 120}
            }))
            result = publish_wait_override(path, "MARKET_DATA", "feed failed",
                                           now="2026-01-01T00:01:00+00:00")
            self.assertEqual(result["signal"], "WAIT")
            self.assertEqual(result["confidence_score"], 0)
            self.assertEqual(result["risk_levels"]["entry_reference"], None)
            self.assertEqual(result["risk_levels"]["stop_loss"], None)
            self.assertEqual(result["safety_override"]["status"], "FAIL_CLOSED")
            self.assertFalse(result["data_quality"]["five_minute_data_fresh"])
            self.assertEqual(json.loads(path.read_text())["signal"], "WAIT")

    def test_existing_dashboard_fields_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "signal_result.json"
            path.write_text(json.dumps({"symbol": "BTCUSDT", "scores": {"LONG": 8}}))
            result = publish_wait_override(path, "NEWS_SCAN", "news unavailable")
            self.assertEqual(result["symbol"], "BTCUSDT")
            self.assertEqual(result["scores"]["LONG"], 8)
            self.assertEqual(result["early_setup"]["direction"], "WAIT")

    def test_missing_or_corrupt_file_still_publishes_wait(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "signal_result.json"
            path.write_text("{broken")
            result = publish_wait_override(path, "RUNNER_EXCEPTION", "bad snapshot")
            self.assertEqual(result["signal"], "WAIT")
            self.assertTrue(path.exists())

    def test_repeated_override_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "signal_result.json"
            first = publish_wait_override(path, "MARKET_DATA", "timeout")
            second = publish_wait_override(path, "MARKET_DATA", "timeout")
            self.assertEqual(second["signal"], "WAIT")
            self.assertEqual(second["warnings"].count(
                "FAIL-CLOSED WAIT: MARKET_DATA failed; timeout"), 1)


if __name__ == "__main__":
    unittest.main()
