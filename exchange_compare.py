#!/usr/bin/env python3
"""Compare public BTC perpetual prices across venues; never substitutes spot."""
import json
import urllib.request
from datetime import datetime, timezone

OUT = "exchange_comparison.json"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "BTC-Signal-Pro-Elite/1.0"})
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    venues = {}
    urls = {
        "Binance USD-M Futures": "https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT",
        "Bybit linear perpetual": "https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT",
        "OKX BTC-USDT-SWAP": "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT-SWAP",
    }
    for name, url in urls.items():
        try:
            data = get(url)
            if name.startswith("Binance"):
                price = float(data["price"])
            elif name.startswith("Bybit"):
                if int(data.get("retCode", -1)) != 0:
                    raise ValueError(data.get("retMsg", "Bybit API error"))
                price = float(data["result"]["list"][0]["lastPrice"])
            else:
                price = float(data["data"][0]["last"])
            venues[name] = {"available": True, "price": price}
        except Exception as exc:
            venues[name] = {"available": False, "error": str(exc)[:180]}
    available = [(k, v["price"]) for k, v in venues.items() if v.get("available")]
    reference_name, reference = next(((k, p) for k, p in available if k.startswith("OKX")),
                                     available[0] if available else (None, None))
    for name, item in venues.items():
        if item.get("available") and reference:
            item["difference_usdt"] = round(item["price"] - reference, 4)
            item["difference_pct"] = round((item["price"] / reference - 1) * 100, 5)
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": "BTCUSDT perpetual contracts only",
        "reference": reference_name,
        "venues": venues,
        "status": "COMPARISON_AVAILABLE" if len(available) >= 2 else "PARTIAL_OR_UNAVAILABLE",
        "warning": "Venue price differences are normal. Compare execution price, spread, mark price and funding on the exchange where you trade. Binance Futures is never replaced with Spot data."
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    main()
