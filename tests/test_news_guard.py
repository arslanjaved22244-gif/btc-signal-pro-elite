import importlib.util
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "news_guard.py"
SPEC = importlib.util.spec_from_file_location("btc_news_guard", PATH)
NEWS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NEWS)


class NewsRiskKeywordTests(unittest.TestCase):
    def test_generic_bitcoin_headline_does_not_trigger_event_lock(self):
        self.assertFalse(any(term in "bitcoin market update today" for term in NEWS.RISK_TERMS))

    def test_material_macro_and_geopolitical_terms_are_retained(self):
        for headline in ("Federal Reserve emergency rate decision", "Iran missile attack",
                         "Trump announces new tariff", "major crypto exchange hack"):
            self.assertTrue(any(term in headline.lower() for term in NEWS.RISK_TERMS), headline)


if __name__ == "__main__":
    unittest.main()
