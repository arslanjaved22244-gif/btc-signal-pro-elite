#!/usr/bin/env python3
"""Public Binance USD-M Futures market-data collector. Read-only; never places orders."""
import json
import time
import urllib.parse
import urllib.request
import urllib.error
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
                raw = response.read()
                if not raw.strip():
                    raise ValueError(f"Empty response from {host} (HTTP {response.status})")
                try:
                    data = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    preview = raw[:180].decode("utf-8", errors="replace").replace("\\n", " ")
                    raise ValueError(f"Invalid JSON from {host}: {exc}; body={preview!r}") from exc
                BASE = host
                return data
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read(180).decode("utf-8", errors="replace")
            except Exception:
                body = ""
            last_error = RuntimeError(f"HTTP {exc.code} from {host}: {body[:180]}")
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


def collect_binance():
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


def bybit_request(path, params=None, timeout=15):
    query = urllib.parse.urlencode(params or {})
    url = "https://api.bybit.com" + path + (("?" + query) if query else "")
    req = urllib.request.Request(url, headers={"User-Agent": "BTC-Signal-Pro-Elite/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
    if not raw.strip():
        raise ValueError("Empty response from Bybit API")
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("retCode") != 0:
        raise RuntimeError("Bybit API error: " + str(payload.get("retCode")) + " " + str(payload.get("retMsg")))
    return payload.get("result", {})


def bybit_feed(path, params=None):
    started = time.time()
    try:
        return {"ok": True, "data": bybit_request(path, params),
                "latency_ms": round((time.time()-started)*1000)}
    except Exception as exc:
        return {"ok": False, "error": str(exc),
                "latency_ms": round((time.time()-started)*1000)}


def bybit_candles(interval, limit=300):
    iv = {"5m":"5", "15m":"15", "1h":"60", "4h":"240", "1d":"D"}[interval]
    result = bybit_request("/v5/market/kline", {
        "category":"linear", "symbol":SYMBOL, "interval":iv, "limit":str(limit)
    })
    now_ms = int(time.time()*1000)
    duration = {"5m":300000,"15m":900000,"1h":3600000,"4h":14400000,"1d":86400000}[interval]
    rows = []
    for r in reversed(result.get("list", [])):
        start = int(r[0])
        if start + duration > now_ms:
            continue
        rows.append({
            "time":start, "open":float(r[1]), "high":float(r[2]),
            "low":float(r[3]), "close":float(r[4]), "volume":float(r[5]),
            "quote_volume":float(r[6]), "trades":0,
            "taker_buy_base":None, "taker_buy_quote":None
        })
    if len(rows) < 50:
        raise ValueError("Insufficient completed Bybit linear Futures candles")
    return rows


def collect_bybit():
    generated = datetime.now(timezone.utc).isoformat()
    result = {
        "system":"BTC Signal Pro Elite Futures Data Engine",
        "version":"5.0-bybit-linear-futures-fallback",
        "symbol":SYMBOL, "market_type":"BYBIT_LINEAR_USDT_PERPETUAL",
        "exchange":"Bybit", "generated_at":generated,
        "source":"https://api.bybit.com/v5/market (linear USDT perpetual)",
        "timeframes":{}, "feeds":{}, "data_quality":{}
    }
    for tf in TIMEFRAMES:
        try:
            rows=bybit_candles(tf)
            result["timeframes"][tf]={"ok":True,"candles":rows,
                "last_closed_candle_time":rows[-1]["time"],
                "age_seconds":max(0,int(time.time()*1000-rows[-1]["time"]-{"5m":300000,"15m":900000,"1h":3600000,"4h":14400000,"1d":86400000}[tf])//1000)}
        except Exception as exc:
            result["timeframes"][tf]={"ok":False,"error":str(exc)}

    ticker=bybit_feed("/v5/market/tickers",{"category":"linear","symbol":SYMBOL})
    result["feeds"]["mark_price_funding"]=ticker
    if ticker["ok"]:
        rows=ticker["data"].get("list",[])
        t=rows[0] if rows else {}
        ticker["data"]={
            "markPrice":t.get("markPrice"),"lastPrice":t.get("lastPrice"),
            "lastFundingRate":t.get("fundingRate"),"nextFundingTime":t.get("nextFundingTime")
        }
    result["feeds"]["open_interest_current"]=bybit_feed(
        "/v5/market/tickers",{"category":"linear","symbol":SYMBOL})
    if result["feeds"]["open_interest_current"]["ok"]:
        rows=result["feeds"]["open_interest_current"]["data"].get("list",[])
        result["feeds"]["open_interest_current"]["data"]={"openInterest":rows[0].get("openInterest") if rows else None}

    result["feeds"]["open_interest_history_5m"]=bybit_feed(
        "/v5/market/open-interest",{"category":"linear","symbol":SYMBOL,"intervalTime":"5min","limit":"30"})
    if result["feeds"]["open_interest_history_5m"]["ok"]:
        result["feeds"]["open_interest_history_5m"]["data"]=result["feeds"]["open_interest_history_5m"]["data"].get("list",[])
    result["feeds"]["taker_flow_5m"]=bybit_feed(
        "/v5/market/account-ratio",{"category":"linear","symbol":SYMBOL,"period":"5min","limit":"30"})
    if result["feeds"]["taker_flow_5m"]["ok"]:
        result["feeds"]["taker_flow_5m"]["data"]=result["feeds"]["taker_flow_5m"]["data"].get("list",[])
    result["feeds"]["order_book"]=bybit_feed(
        "/v5/market/orderbook",{"category":"linear","symbol":SYMBOL,"limit":"50"})
    if result["feeds"]["order_book"]["ok"]:
        ob=result["feeds"]["order_book"]["data"]
        result["feeds"]["order_book"]["data"]={"bids":ob.get("b",[]),"asks":ob.get("a",[])}
    result["feeds"]["recent_agg_trades"]=bybit_feed(
        "/v5/market/recent-trade",{"category":"linear","symbol":SYMBOL,"limit":"1000"})
    if result["feeds"]["recent_agg_trades"]["ok"]:
        trades=result["feeds"]["recent_agg_trades"]["data"].get("list",[])
        result["feeds"]["recent_agg_trades"]["data"]=[
            {"q":x.get("size",0),"m":x.get("side")=="Sell","price":x.get("price"),"time":x.get("time")}
            for x in trades]
    result["feeds"]["funding_history"]=bybit_feed(
        "/v5/market/funding/history",{"category":"linear","symbol":SYMBOL,"limit":"10"})
    if result["feeds"]["funding_history"]["ok"]:
        result["feeds"]["funding_history"]["data"]=result["feeds"]["funding_history"]["data"].get("list",[])

    trades=result["feeds"]["recent_agg_trades"]
    if trades["ok"]:
        buys=sells=0.0
        for trade in trades["data"]:
            qty=float(trade.get("q",0) or 0)
            if trade.get("m"): sells+=qty
            else: buys+=qty
        result["derived"]={"cvd_base":round(buys-sells,8),
            "aggressive_buy_base":round(buys,8),"aggressive_sell_base":round(sells,8),
            "trade_count":len(trades["data"]),
            "cvd_note":"Rolling snapshot from latest Bybit linear perpetual trades, not historical exchange-wide CVD."}
    else:
        result["derived"]={"cvd_base":None,"note":"CVD unavailable because Bybit recent trades failed."}
    book=result["feeds"]["order_book"]
    if book["ok"]:
        bids=sum(float(x[1]) for x in book["data"].get("bids",[]))
        asks=sum(float(x[1]) for x in book["data"].get("asks",[]))
        total=bids+asks
        result["derived"].update({"visible_depth_bid_base":round(bids,8),
            "visible_depth_ask_base":round(asks,8),
            "order_book_imbalance":round((bids-asks)/total,6) if total else None,
            "order_book_note":"Bybit top-50 visible levels only; depth changes rapidly."})

    result["feeds"]["liquidations"]={"ok":False,"status":"not_collected",
        "reason":"The existing Binance force-order WebSocket is not a Bybit liquidation feed; no cross-exchange substitution is made."}
    core=all(result["timeframes"].get(tf,{}).get("ok") for tf in TIMEFRAMES)
    fresh=result["timeframes"].get("5m",{}).get("age_seconds",999999)<=600
    result["data_quality"]={"core_timeframes_ok":core,"five_minute_data_fresh":fresh,
        "futures_only":True,"status":"HEALTHY" if core and fresh else "DEGRADED","warnings":[]}
    if not core: result["data_quality"]["warnings"].append("One or more Bybit linear Futures candle timeframes failed.")
    if not fresh: result["data_quality"]["warnings"].append("5m Bybit linear Futures candle data is stale.")
    for key,feed in result["feeds"].items():
        if key!="liquidations" and not feed.get("ok"):
            result["data_quality"]["warnings"].append(key+" feed unavailable: "+feed.get("error","unknown"))
    result["data_quality"]["warnings"].append("Using Bybit linear USDT perpetual reference data because Binance API returned empty HTTP 202 responses. Verify Binance execution price independently.")
    result["data_quality"]["warnings"].append("Liquidation monitor remains Binance-only and is not treated as Bybit liquidation coverage.")
    return result


def collect():
    primary=collect_binance()
    if primary.get("data_quality",{}).get("core_timeframes_ok") and primary.get("data_quality",{}).get("five_minute_data_fresh"):
        return primary
    try:
        fallback=collect_bybit()
        if fallback.get("data_quality",{}).get("core_timeframes_ok") and fallback.get("data_quality",{}).get("five_minute_data_fresh"):
            fallback["fallback_from"]="Binance USD-M Futures"
            fallback["binance_failure_summary"]=primary.get("data_quality",{}).get("warnings",[])
            return fallback
        fallback["data_quality"]["warnings"].append("Bybit fallback also failed core data checks; Binance diagnostics retained below.")
        fallback["binance_failure_summary"]=primary.get("data_quality",{}).get("warnings",[])
        return fallback
    except Exception as exc:
        primary["data_quality"]["warnings"].append("Bybit linear Futures fallback failed: "+str(exc))
        return primary
