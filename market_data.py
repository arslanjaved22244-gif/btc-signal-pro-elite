#!/usr/bin/env python3
"""Public Binance USD-M Futures market-data collector. Read-only; never places orders."""
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASES = ["https://fapi.binance.com", "https://fapi1.binance.com", "https://fapi2.binance.com", "https://fapi3.binance.com", "https://fapi4.binance.com"]
BASE = BASES[0]
SYMBOL = "BTCUSDT"
OUT = "market_data.json"
TIMEFRAMES = ("5m", "15m", "1h", "4h", "1d")


def request_json(path, params=None, timeout=15):
    query = urllib.parse.urlencode(params or {})
    global BASE
    last_error = None
    for host in [BASE] + [x for x in BASES if x != BASE]:
        url = host + path + (("?" + query) if query else "")
        req = urllib.request.Request(url, headers={"User-Agent": "BTC-Signal-Pro-Elite/4.1"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
                BASE = host
                return data
        except Exception as exc:
            last_error = exc
    raise last_error


def safe_fetch(name, path, params=None):
    started = time.time()
    try:
        data = request_json(path, params)
        return {"ok": True, "data": data, "latency_ms": round((time.time() - started) * 1000)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "latency_ms": round((time.time() - started) * 1000)}


def candles(interval, limit=300):
    raw = request_json("/fapi/v1/klines", {"symbol": SYMBOL, "interval": interval, "limit": limit})
    now_ms = int(time.time() * 1000)
    rows = []
    for r in raw:
        # Keep completed candles only; an in-progress candle can distort indicators.
        if int(r[6]) >= now_ms:
            continue
        rows.append({
            "time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
            "low": float(r[3]), "close": float(r[4]), "volume": float(r[5]),
            "quote_volume": float(r[7]), "trades": int(r[8]),
            "taker_buy_base": float(r[9]), "taker_buy_quote": float(r[10])
        })
    if len(rows) < 50:
        raise ValueError("Insufficient completed candles")
    return rows


def collect():
    generated = datetime.now(timezone.utc).isoformat()
    result = {
        "system": "BTC Signal Pro Elite Futures Data Engine",
        "version": "4.0-market-data-foundation",
        "symbol": SYMBOL, "market_type": "BINANCE_USDM_FUTURES",
        "generated_at": generated, "source": BASE,
        "timeframes": {}, "feeds": {}, "data_quality": {}
    }

    # Futures OHLCV across all requested timeframes. No spot fallback is mixed in.
    for tf in TIMEFRAMES:
        try:
            rows = candles(tf)
            result["timeframes"][tf] = {
                "ok": True, "candles": rows,
                "last_closed_candle_time": rows[-1]["time"],
                "age_seconds": max(0, int(time.time() * 1000 - rows[-1]["time"]) // 1000)
            }
        except Exception as exc:
            result["timeframes"][tf] = {"ok": False, "error": str(exc)}

    result["feeds"]["mark_price_funding"] = safe_fetch(
        "mark_price_funding", "/fapi/v1/premiumIndex", {"symbol": SYMBOL})
    result["feeds"]["open_interest_current"] = safe_fetch(
        "open_interest_current", "/fapi/v1/openInterest", {"symbol": SYMBOL})
    result["feeds"]["open_interest_history_5m"] = safe_fetch(
        "open_interest_history_5m", "/futures/data/openInterestHist",
        {"symbol": SYMBOL, "period": "5m", "limit": 30})
    result["feeds"]["taker_flow_5m"] = safe_fetch(
        "taker_flow_5m", "/futures/data/takerlongshortRatio",
        {"symbol": SYMBOL, "period": "5m", "limit": 30})
    result["feeds"]["order_book"] = safe_fetch(
        "order_book", "/fapi/v1/depth", {"symbol": SYMBOL, "limit": 100})
    result["feeds"]["recent_agg_trades"] = safe_fetch(
        "recent_agg_trades", "/fapi/v1/aggTrades", {"symbol": SYMBOL, "limit": 1000})
    result["feeds"]["funding_history"] = safe_fetch(
        "funding_history", "/fapi/v1/fundingRate", {"symbol": SYMBOL, "limit": 10})

    # Derive signed taker flow from Binance aggregate trades. m=true means
    # buyer was maker, therefore the aggressive side was the seller.
    trades = result["feeds"]["recent_agg_trades"]
    if trades["ok"] and isinstance(trades["data"], list):
        buy_qty = sell_qty = 0.0
        for trade in trades["data"]:
            qty = float(trade.get("q", 0))
            if trade.get("m") is True:
                sell_qty += qty
            else:
                buy_qty += qty
        result["derived"] = {
            "cvd_base": round(buy_qty - sell_qty, 8),
            "aggressive_buy_base": round(buy_qty, 8),
            "aggressive_sell_base": round(sell_qty, 8),
            "trade_count": len(trades["data"]),
            "cvd_note": "CVD is derived from the latest 1000 aggregate trades; it is a rolling snapshot, not exchange-wide historical CVD."
        }
    else:
        result["derived"] = {"cvd_base": None, "note": "CVD unavailable because aggregate-trade data failed."}

    # Order-book imbalance from visible top-100 levels, not full-market depth.
    book = result["feeds"]["order_book"]
    if book["ok"] and isinstance(book["data"], dict):
        bids = sum(float(level[1]) for level in book["data"].get("bids", []))
        asks = sum(float(level[1]) for level in book["data"].get("asks", []))
        total = bids + asks
        result["derived"]["visible_depth_bid_base"] = round(bids, 8)
        result["derived"]["visible_depth_ask_base"] = round(asks, 8)
        result["derived"]["order_book_imbalance"] = round((bids - asks) / total, 6) if total else None
        result["derived"]["order_book_note"] = "Top-100 visible levels only; order book can change rapidly."

    # Public REST does not provide a dependable historical liquidation tape.
    # Do not fabricate liquidation totals from unrelated endpoints.
    result["feeds"]["liquidations"] = {
        "ok": False,
        "status": "not_collected",
        "reason": "No dependable public historical liquidation REST feed is used. A live force-order WebSocket collector is a separate service."
    }

    core_ok = all(result["timeframes"].get(tf, {}).get("ok") for tf in ("5m", "15m", "1h", "4h", "1d"))
    fresh_5m = result["timeframes"].get("5m", {}).get("age_seconds", 999999) <= 600
    result["data_quality"] = {
        "core_timeframes_ok": core_ok,
        "five_minute_data_fresh": fresh_5m,
        "futures_only": True,
        "status": "HEALTHY" if core_ok and fresh_5m else "DEGRADED",
        "warnings": []
    }
    if not core_ok:
        result["data_quality"]["warnings"].append("One or more Futures candle timeframes failed.")
    if not fresh_5m:
        result["data_quality"]["warnings"].append("5m Futures candle data is stale.")
    for key, feed in result["feeds"].items():
        if key != "liquidations" and not feed.get("ok"):
            result["data_quality"]["warnings"].append(key + " feed unavailable: " + feed.get("error", "unknown"))
    if result["feeds"]["liquidations"]["status"] == "not_collected":
        result["data_quality"]["warnings"].append("Liquidation feed is not connected yet.")
    return result


def main():
    payload = collect()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    summary = {
        "status": payload["data_quality"]["status"],
        "timeframes": {k: v.get("ok", False) for k, v in payload["timeframes"].items()},
        "feeds": {k: v.get("ok", False) for k, v in payload["feeds"].items()},
        "generated_at": payload["generated_at"]
    }
    print(json.dumps(summary, indent=2))
    if not payload["data_quality"]["core_timeframes_ok"]:
        print("WARNING: Core Binance USD-M Futures data unavailable. Downstream engine must publish WAIT; no Spot fallback is permitted.")


if __name__ == "__main__":
    main()
