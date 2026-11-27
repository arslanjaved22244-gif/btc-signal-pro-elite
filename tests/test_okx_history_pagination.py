import unittest
from unittest.mock import patch

import market_data


def candle_row(timestamp):
    return [
        str(timestamp), "60000", "60100", "59900", "60050",
        "12.5", "750000", "740000", "1"
    ]


class OkxHistoryPaginationTests(unittest.TestCase):
    @patch.object(market_data, "okx_request")
    def test_five_minute_history_fetches_multiple_pages(self, request):
        # Each response represents one older page of 300 completed candles.
        def page(_path, params):
            page_number = request.call_count - 1
            newest = 1_800_000_000_000 - page_number * 300 * 300_000
            return [
                candle_row(newest - index * 300_000)
                for index in range(300)
            ]

        request.side_effect = page
        rows = market_data.okx_candles("5m")

        self.assertEqual(request.call_count, 10)
        self.assertEqual(len(rows), 3000)
        self.assertEqual(rows, sorted(rows, key=lambda item: item["time"]))
        self.assertIn("after", request.call_args_list[1].args[1])

    @patch.object(market_data, "okx_request")
    def test_higher_timeframes_keep_single_page(self, request):
        newest = 1_800_000_000_000
        request.return_value = [
            candle_row(newest - index * 900_000)
            for index in range(300)
        ]

        rows = market_data.okx_candles("15m")

        self.assertEqual(request.call_count, 1)
        self.assertEqual(len(rows), 300)


if __name__ == "__main__":
    unittest.main()
