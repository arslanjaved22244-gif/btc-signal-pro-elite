import importlib.util
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "signal_engine.py"
SPEC = importlib.util.spec_from_file_location("btc_signal_engine_freshness", PATH)
ENGINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ENGINE)


class DerivativesFreshnessTests(unittest.TestCase):
    def test_recent_millisecond_timestamps_are_kept(self):
        now = 1_800_000_000_000
        rows = [{"timestamp": now - 5 * 60_000, "sumOpenInterest": 100}]
        self.assertEqual(ENGINE.fresh_5m_derivative_rows(rows, now_ms=now), rows)

    def test_stale_derivatives_rows_are_excluded(self):
        now = 1_800_000_000_000
        rows = [{"timestamp": now - 20 * 60_000, "sumOpenInterest": 100}]
        self.assertEqual(ENGINE.fresh_5m_derivative_rows(rows, now_ms=now), [])

    def test_untimestamped_rows_are_excluded(self):
        rows = [{"sumOpenInterest": 100}]
        self.assertEqual(ENGINE.fresh_5m_derivative_rows(rows, now_ms=1_800_000_000_000), [])

    def test_second_timestamps_are_supported(self):
        now = 1_800_000_000_000
        rows = [{"timestamp": (now - 5 * 60_000) // 1000}]
        self.assertEqual(ENGINE.fresh_5m_derivative_rows(rows, now_ms=now), rows)


if __name__ == "__main__":
    unittest.main()
