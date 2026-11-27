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
        bids = book["data"].get("bids", [])
        asks = book["data"].get("asks", [])
        bid_notional = sum(float(level[0]) * float(level[1]) for level in bids[:20])
        ask_notional = sum(float(level[0]) * float(level[1]) for level in asks[:20])
        result["derived"]["visible_depth_bid_notional_usdt"] = round(bid_notional, 2)
        result["derived"]["visible_depth_ask_notional_usdt"] = round(ask_notional, 2)
        result["derived"]["order_book_imbalance"] = normalized_order_book_imbalance(bids, asks, depth=20)
        result["derived"]["order_book_imbalance_method"] = "Top-20 visible levels; quote-notional (price × quantity); (bid-ask)/(bid+ask)"
        result["derived"]["order_book_note"] = "Top-20 visible quote-notional depth; rapidly changing snapshot, not full-market liquidity."

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
        result["feeds"]["open_interest_history_5m"]["data"]=[
            {"sumOpenInterest":x.get("openInterest",0),"timestamp":x.get("timestamp")}
            for x in result["feeds"]["open_interest_history_5m"]["data"].get("list",[])]
    # Bybit account-ratio is long/short account positioning, NOT executed
    # taker buy/sell volume. Keep it separately labelled and never feed it to
    # the taker-flow signal feature.
    account_ratio = bybit_feed(
        "/v5/market/account-ratio",{"category":"linear","symbol":SYMBOL,"period":"5min","limit":"30"})
    if account_ratio["ok"]:
        account_ratio["data"] = [
            {"long_short_account_ratio": (
                float(x.get("buyRatio", 0) or 0) / float(x.get("sellRatio", 1) or 1)
                if float(x.get("sellRatio", 0) or 0) > 0 else None
             ), "long_account_ratio": x.get("buyRatio"),
             "short_account_ratio": x.get("sellRatio"), "timestamp": x.get("timestamp")}
            for x in account_ratio["data"].get("list", [])
        ]
    result["feeds"]["account_position_ratio_5m"] = account_ratio
    result["feeds"]["taker_flow_5m"] = {
        "ok": False,
        "error": "Bybit account-ratio measures account positioning, not executed taker buy/sell volume; excluded from taker-flow signals."
    }
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
        bids=book["data"].get("bids",[])
        asks=book["data"].get("asks",[])
        bid_notional=sum(float(x[0])*float(x[1]) for x in bids[:20])
        ask_notional=sum(float(x[0])*float(x[1]) for x in asks[:20])
        result["derived"].update({
            "visible_depth_bid_notional_usdt":round(bid_notional,2),
            "visible_depth_ask_notional_usdt":round(ask_notional,2),
            "order_book_imbalance":normalized_order_book_imbalance(bids,asks,depth=20),
            "order_book_imbalance_method":"Top-20 visible levels; quote-notional (price × quantity); (bid-ask)/(bid+ask)",
            "order_book_note":"Bybit top-20 visible quote-notional depth; rapidly changing snapshot."
        })

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



def okx_request(path, params=None, timeout=15):
    query = urllib.parse.urlencode(params or {})
    url = "https://www.okx.com" + path + (("?" + query) if query else "")
    req = urllib.request.Request(url, headers={"User-Agent":"BTC-Signal-Pro-Elite/5.1","Accept":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw=response.read()
    except urllib.error.HTTPError as exc:
        try:
            detail=exc.read(400).decode("utf-8",errors="replace")
        except Exception:
            detail=""
        raise RuntimeError(f"OKX HTTP {exc.code}: {detail[:300]}") from exc
    if not raw.strip():
        raise ValueError("Empty response from OKX public API")
    payload=json.loads(raw.decode("utf-8"))
    if payload.get("code")!="0":
        raise RuntimeError("OKX API error: "+str(payload.get("code"))+" "+str(payload.get("msg")))
    return payload.get("data",[])


def okx_feed(path, params=None):
    started=time.time()
    try:
        return {"ok":True,"data":okx_request(path,params),
                "latency_ms":round((time.time()-started)*1000)}
    except Exception as exc:
        return {"ok":False,"error":str(exc),
                "latency_ms":round((time.time()-started)*1000)}


def okx_candles(interval, limit=300):
    bar={"5m":"5m","15m":"15m","1h":"1H","4h":"4H","1d":"1D"}[interval]
    # The 5m backtest needs more than one OKX history page. Walk backward
    # using the oldest timestamp returned; never include the live candle.
    raw_all=[]
    cursor=None
    pages=2 if interval=="5m" else 1
    for _ in range(pages):
        params={"instId":"BTC-USDT-SWAP","bar":bar,"limit":str(limit)}
        if cursor is not None:
            params["after"]=str(cursor)
        batch=okx_request("/api/v5/market/history-candles",params)
        if not batch:
            break
        raw_all.extend(batch)
        oldest=min(int(x[0]) for x in batch)
        if len(batch)<limit or oldest<=0:
            break
        cursor=oldest
    by_time={int(x[0]):x for x in raw_all if len(x)>=9}
    rows=[]
    for r in sorted(by_time.values(),key=lambda x:int(x[0])):
        if str(r[8])!="1":
            continue
        rows.append({"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),
            "low":float(r[3]),"close":float(r[4]),"volume":float(r[5]),
            "quote_volume":float(r[7]),"trades":0,
            "taker_buy_base":None,"taker_buy_quote":None})
    if len(rows)<50:
        raise ValueError("Insufficient completed OKX BTC-USDT-SWAP candles")
    return rows


def collect_okx():
    generated=datetime.now(timezone.utc).isoformat()
    result={"system":"BTC Signal Pro Elite Futures Data Engine",
        "version":"5.2-okx-swap-fallback","symbol":"BTC-USDT-SWAP",
        "market_type":"OKX_BTC_USDT_SWAP","exchange":"OKX",
        "generated_at":generated,
        "source":"https://www.okx.com/api/v5/market (BTC-USDT-SWAP perpetual)",
        "timeframes":{},"feeds":{},"data_quality":{}}
    durations={"5m":300000,"15m":900000,"1h":3600000,"4h":14400000,"1d":86400000}
    for tf in TIMEFRAMES:
        try:
            rows=okx_candles(tf)
            result["timeframes"][tf]={"ok":True,"candles":rows,
                "last_closed_candle_time":rows[-1]["time"],
                "age_seconds":max(0,int((time.time()*1000-rows[-1]["time"]-durations[tf])/1000))}
        except Exception as exc:
            result["timeframes"][tf]={"ok":False,"error":str(exc)}

    ticker=okx_feed("/api/v5/market/ticker",{"instId":"BTC-USDT-SWAP"})
    if ticker["ok"]:
        t=ticker["data"][0] if ticker["data"] else {}
        ticker["data"]={"lastPrice":t.get("last"),"markPrice":t.get("last")}
    result["feeds"]["mark_price_funding"]=ticker

    funding=okx_feed("/api/v5/public/funding-rate",{"instId":"BTC-USDT-SWAP"})
    if funding["ok"]:
        x=funding["data"][0] if funding["data"] else {}
        result["feeds"]["mark_price_funding"]["data"]["lastFundingRate"]=x.get("fundingRate")
        result["feeds"]["mark_price_funding"]["data"]["nextFundingTime"]=x.get("nextFundingTime")

    oi=okx_feed("/api/v5/public/open-interest",{"instType":"SWAP","instId":"BTC-USDT-SWAP"})
    if oi["ok"]:
        x=oi["data"][0] if oi["data"] else {}
        result["feeds"]["open_interest_current"]={"ok":True,"data":{"openInterest":x.get("oi"),"openInterestCurrency":x.get("oiCcy")}}
    else:
        result["feeds"]["open_interest_current"]=oi
    oi_hist=okx_feed("/api/v5/rubik/stat/contracts/open-interest-history",{
        "instId":"BTC-USDT-SWAP","period":"5m","limit":"30"})
    if oi_hist["ok"]:
        normalized=[]
        for point in oi_hist["data"]:
            # OKX Rubik endpoints commonly return positional arrays:
            # [ts, oi contracts, oi coin, oi USD], but some responses use objects.
            if isinstance(point, dict):
                value=point.get("oiUsd",point.get("oi",point.get("oiCcy",0)))
                timestamp=point.get("ts")
            elif isinstance(point, (list, tuple)) and len(point)>=4:
                value=point[3] or point[2] or point[1]
                timestamp=point[0]
            else:
                continue
            normalized.append({"sumOpenInterest":value,"timestamp":timestamp})
        oi_hist["data"]=list(reversed(normalized))
        if len(oi_hist["data"])<2:
            oi_hist={"ok":False,"error":"OKX returned fewer than two 5m open-interest history points."}
    result["feeds"]["open_interest_history_5m"]=oi_hist

    # OKX Rubik taker-volume uses instType=CONTRACTS (not SWAP).
    # Public response rows are [timestamp, sell volume, buy volume].
    taker=okx_feed("/api/v5/rubik/stat/taker-volume",{
        "ccy":"BTC","instType":"CONTRACTS","period":"5m"})
    if taker["ok"]:
        normalized=[]
        for point in taker["data"]:
            if isinstance(point, dict):
                timestamp=point.get("ts")
                sell=float(point.get("sellVol",0) or 0)
                buy=float(point.get("buyVol",0) or 0)
            elif isinstance(point, (list, tuple)) and len(point)>=3:
                timestamp=point[0]
                sell=float(point[1] or 0)
                buy=float(point[2] or 0)
            else:
                continue
            normalized.append({"buySellRatio":buy/sell if sell>0 else None,
                "buyVol":buy,"sellVol":sell,"timestamp":timestamp})
        taker["data"]=list(reversed(normalized))
        if not taker["data"]:
            taker={"ok":False,"error":"OKX returned no 5m taker-volume points."}
    result["feeds"]["taker_flow_5m"]=taker

    book=okx_feed("/api/v5/market/books",{"instId":"BTC-USDT-SWAP","sz":"50"})
    if book["ok"]:
        x=book["data"][0] if book["data"] else {}
        book["data"]={"bids":x.get("bids",[]),"asks":x.get("asks",[])}
    result["feeds"]["order_book"]=book

    trades=okx_feed("/api/v5/market/trades",{"instId":"BTC-USDT-SWAP","limit":"500"})
    if trades["ok"]:
        trades["data"]=[{"q":x.get("sz",0),"m":x.get("side")=="sell",
            "price":x.get("px"),"time":x.get("ts")} for x in trades["data"]]
    result["feeds"]["recent_agg_trades"]=trades
    result["feeds"]["funding_history"]=okx_feed("/api/v5/public/funding-rate-history",{"instId":"BTC-USDT-SWAP","limit":"10"})

    if trades["ok"]:
        buys=sells=0.0
        for x in trades["data"]:
            q=float(x.get("q",0) or 0)
            if x.get("m"): sells+=q
            else: buys+=q
        result["derived"]={"cvd_base":round(buys-sells,8),
            "aggressive_buy_base":round(buys,8),"aggressive_sell_base":round(sells,8),
            "trade_count":len(trades["data"]),
            "cvd_note":"Rolling snapshot from latest OKX swap trades."}
    else:
        result["derived"]={"cvd_base":None,"note":"OKX trade feed unavailable."}
    if book["ok"]:
        bids=book["data"].get("bids",[])
        asks=book["data"].get("asks",[])
        bid_notional=sum(float(x[0])*float(x[1]) for x in bids[:20])
        ask_notional=sum(float(x[0])*float(x[1]) for x in asks[:20])
        result["derived"].update({
            "visible_depth_bid_notional_usdt":round(bid_notional,2),
            "visible_depth_ask_notional_usdt":round(ask_notional,2),
            "order_book_imbalance":normalized_order_book_imbalance(bids,asks,depth=20),
            "order_book_imbalance_method":"Top-20 visible levels; quote-notional (price × quantity); (bid-ask)/(bid+ask)",
            "order_book_note":"OKX top-20 visible quote-notional depth; rapidly changing snapshot."
        })

    result["feeds"]["liquidations"]={"ok":False,"status":"not_collected",
        "reason":"No OKX liquidation stream is connected; Binance liquidation sample is not reused."}
    core=all(result["timeframes"].get(tf,{}).get("ok") for tf in TIMEFRAMES)
    fresh=result["timeframes"].get("5m",{}).get("age_seconds",999999)<=600
    result["data_quality"]={"core_timeframes_ok":core,"five_minute_data_fresh":fresh,
        "futures_only":True,"status":"HEALTHY" if core and fresh else "DEGRADED","warnings":[]}
    if not core: result["data_quality"]["warnings"].append("One or more OKX BTC-USDT-SWAP candle timeframes failed.")
    if not fresh: result["data_quality"]["warnings"].append("OKX 5m Futures candle data is stale.")
    # Completed, fresh Futures candles are the mandatory baseline. Derivatives
    # feeds are optional confluence: missing OI/taker history must be disclosed,
    # but must not silently veto every signal when price data is healthy.
    derivatives_ok=all(result["feeds"].get(k,{}).get("ok") for k in
        ("mark_price_funding","open_interest_current","open_interest_history_5m",
         "taker_flow_5m","order_book","recent_agg_trades"))
    result["data_quality"]["derivatives_confluence_status"]="AVAILABLE" if derivatives_ok else "PARTIAL"
    if not derivatives_ok:
        result["data_quality"]["warnings"].append("Some derivatives confluence feeds are unavailable; signals rely more heavily on verified multi-timeframe Futures candles and available flow data.")
    for key,feed in result["feeds"].items():
        if key not in ("liquidations",) and not feed.get("ok"):
            result["data_quality"]["warnings"].append(key+" feed unavailable: "+feed.get("error","not configured"))
    result["data_quality"]["warnings"].append("OKX BTC-USDT-SWAP is a separate exchange perpetual reference; confirm execution venue price and liquidity.")
    return result



def normalized_order_book_imbalance(bids, asks, depth=20):
    """Quote-notional imbalance over the first N visible price levels."""
    bid_notional = sum(float(level[0]) * float(level[1]) for level in bids[:depth])
    ask_notional = sum(float(level[0]) * float(level[1]) for level in asks[:depth])
    total = bid_notional + ask_notional
    return round((bid_notional - ask_notional) / total, 6) if total else None


def bitget_request(path, params=None, timeout=15):
    query = urllib.parse.urlencode(params or {})
    url = "https://api.bitget.com" + path + (("?" + query) if query else "")
    req = urllib.request.Request(url, headers={"User-Agent": "BTC-Signal-Pro-Elite/6.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("code") != "00000":
        raise RuntimeError("Bitget API error: " + str(payload.get("code")) + " " + str(payload.get("msg")))
    return payload.get("data")


def bitget_feed(name, path, params=None):
    started = time.time()
    try:
        return {"ok": True, "data": bitget_request(path, params),
                "latency_ms": round((time.time() - started) * 1000)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "latency_ms": round((time.time() - started) * 1000)}


def bitget_candles(interval):
    granularity = {"5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}[interval]
    duration = {"5m": 300000, "15m": 900000, "1h": 3600000, "4h": 14400000, "1d": 86400000}[interval]
    params = {"symbol": SYMBOL, "productType": "USDT-FUTURES",
              "granularity": granularity, "limit": "200"}
    raw = bitget_request("/api/v2/mix/market/candles", params)
    # Bitget's latest-candles endpoint is capped at 200 rows. Page backwards
    # through history-candles so EMA200 and the 300+ candle holdout backtest
    # have enough completed observations instead of silently stalling at 199.
    combined = list(raw or [])
    seen = {int(row[0]) for row in combined if row}
    for _ in range(2):
        if not combined:
            break
        oldest = min(int(row[0]) for row in combined if row)
        older = bitget_request("/api/v2/mix/market/history-candles", {
            "symbol": SYMBOL, "productType": "USDT-FUTURES",
            "granularity": granularity, "endTime": str(oldest - 1), "limit": "200"
        })
        added = 0
        for row in older or []:
            stamp = int(row[0])
            if stamp not in seen:
                combined.append(row)
                seen.add(stamp)
                added += 1
        if not added:
            break
    now_ms = int(time.time() * 1000)
    rows = []
    for row in combined:
        stamp = int(row[0])
        if stamp + duration > now_ms:
            continue
        rows.append({
            "time": stamp, "open": float(row[1]), "high": float(row[2]),
            "low": float(row[3]), "close": float(row[4]),
            "volume": float(row[5]), "quote_volume": float(row[6]) if len(row) > 6 else 0.0,
            "trades": 0, "taker_buy_base": None, "taker_buy_quote": None
        })
    rows.sort(key=lambda item: item["time"])
    if len(rows) < 50:
        raise ValueError("Insufficient completed Bitget USDT Futures candles for " + interval)
    return rows


def collect_bitget():
    generated = datetime.now(timezone.utc).isoformat()
    result = {
        "system": "BTC Signal Pro Elite Futures Data Engine",
        "version": "6.0-bitget-usdt-futures-fallback",
        "symbol": SYMBOL, "market_type": "BITGET_USDT_FUTURES",
        "exchange": "Bitget", "generated_at": generated,
        "source": "https://api.bitget.com/api/v2/mix/market (USDT-FUTURES)",
        "timeframes": {}, "feeds": {}, "data_quality": {}
    }
    durations = {"5m":300000,"15m":900000,"1h":3600000,"4h":14400000,"1d":86400000}
    for tf in TIMEFRAMES:
        try:
            rows = bitget_candles(tf)
            result["timeframes"][tf] = {
                "ok": True, "candles": rows, "last_closed_candle_time": rows[-1]["time"],
                "age_seconds": max(0, int((time.time()*1000 - rows[-1]["time"] - durations[tf]) / 1000))
            }
        except Exception as exc:
            result["timeframes"][tf] = {"ok": False, "error": str(exc)}

    ticker = bitget_feed("ticker", "/api/v2/mix/market/ticker",
                         {"productType":"USDT-FUTURES","symbol":SYMBOL})
    if ticker["ok"]:
        t = (ticker["data"] or [{}])[0]
        ticker["data"] = {
            "lastPrice": t.get("lastPr"), "markPrice": t.get("markPrice"),
            "lastFundingRate": t.get("fundingRate")
        }
    result["feeds"]["mark_price_funding"] = ticker

    oi = bitget_feed("open_interest", "/api/v2/mix/market/open-interest",
                     {"productType":"USDT-FUTURES","symbol":SYMBOL})
    if oi["ok"]:
        values = (oi["data"] or {}).get("openInterestList", [])
        oi["data"] = {"openInterest": values[0].get("size") if values else None}
    result["feeds"]["open_interest_current"] = oi
    result["feeds"]["open_interest_history_5m"] = {
        "ok": False, "error": "Bitget historical OI series is not collected by this fallback; current OI only."
    }

    book = bitget_feed("order_book", "/api/v2/mix/market/merge-depth",
                       {"productType":"USDT-FUTURES","symbol":SYMBOL,"limit":"50"})
    if book["ok"]:
        raw_book = book["data"] or {}
        bids, asks = raw_book.get("bids", []), raw_book.get("asks", [])
        book["data"] = {"bids": bids, "asks": asks}
        bid_notional = sum(float(x[0])*float(x[1]) for x in bids[:20])
        ask_notional = sum(float(x[0])*float(x[1]) for x in asks[:20])
        result["derived"] = {
            "visible_depth_bid_notional_usdt": round(bid_notional, 2),
            "visible_depth_ask_notional_usdt": round(ask_notional, 2),
            "order_book_imbalance": normalized_order_book_imbalance(bids, asks, depth=20),
            "order_book_imbalance_method": "Top-20 visible levels; quote-notional (price × quantity); (bid-ask)/(bid+ask)"
        }
    else:
        result["derived"] = {"cvd_base": None, "order_book_imbalance": None}

    fills = bitget_feed("recent_fills", "/api/v2/mix/market/fills",
                        {"productType":"USDT-FUTURES","symbol":SYMBOL,"limit":"100"})
    if fills["ok"]:
        normalized = []
        buy_qty = sell_qty = 0.0
        for item in fills["data"] or []:
            qty = float(item.get("size", 0) or 0)
            is_sell = str(item.get("side", "")).lower() == "sell"
            sell_qty += qty if is_sell else 0.0
            buy_qty += qty if not is_sell else 0.0
            normalized.append({"q": qty, "m": is_sell, "price": item.get("price"), "time": item.get("ts")})
        fills["data"] = normalized
        result["derived"]["cvd_base"] = round(buy_qty - sell_qty, 8)
        result["derived"]["aggressive_buy_base"] = round(buy_qty, 8)
        result["derived"]["aggressive_sell_base"] = round(sell_qty, 8)
        result["derived"]["trade_count"] = len(normalized)
        result["feeds"]["taker_flow_5m"] = {
            "ok": True, "data": [{"buySellRatio": buy_qty/sell_qty if sell_qty else None,
                                    "buyVol": buy_qty, "sellVol": sell_qty}]
        }
    else:
        result["derived"]["cvd_base"] = None
        result["feeds"]["taker_flow_5m"] = fills
    result["feeds"]["recent_agg_trades"] = fills
    funding = bitget_feed("funding_history", "/api/v2/mix/market/current-fund-rate",
                          {"productType":"USDT-FUTURES","symbol":SYMBOL})
    result["feeds"]["funding_history"] = funding
    if funding["ok"]:
        rows = funding["data"] or []
        if isinstance(rows, list):
            rate = rows[0].get("fundingRate") if rows else None
        else:
            rate = rows.get("fundingRate")
        result["feeds"]["mark_price_funding"]["data"]["lastFundingRate"] = rate
    result["feeds"]["liquidations"] = {
        "ok": False, "status": "not_collected",
        "reason": "No Bitget liquidation stream is connected; Binance sampled forceOrder events are not substituted."
    }
    core = all(result["timeframes"].get(tf, {}).get("ok") for tf in TIMEFRAMES)
    fresh = result["timeframes"].get("5m", {}).get("age_seconds", 999999) <= 600
    result["data_quality"] = {
        "core_timeframes_ok": core, "five_minute_data_fresh": fresh,
        "futures_only": True, "status": "HEALTHY" if core and fresh else "DEGRADED",
        "warnings": ["Bitget USDT-FUTURES is a separate venue; verify execution venue price and liquidity."]
    }
    if not core: result["data_quality"]["warnings"].append("One or more Bitget Futures candle timeframes failed.")
    if not fresh: result["data_quality"]["warnings"].append("Bitget 5m Futures candle data is stale.")
    return result


def collect():
    primary=collect_binance()
    if primary.get("data_quality",{}).get("core_timeframes_ok") and primary.get("data_quality",{}).get("five_minute_data_fresh"):
        return primary

    failures=list(primary.get("data_quality",{}).get("warnings",[]))
    bybit=None
    try:
        bybit=collect_bybit()
        if bybit.get("data_quality",{}).get("core_timeframes_ok") and bybit.get("data_quality",{}).get("five_minute_data_fresh"):
            bybit["fallback_from"]="Binance USD-M Futures"
            bybit["prior_source_failures"]=failures
            return bybit
        bybit_errors={k:v.get("error") for k,v in bybit.get("timeframes",{}).items() if v.get("error")}
        failures.append("Bybit fallback incomplete: "+str(bybit_errors))
    except Exception as exc:
        failures.append("Bybit linear Futures fallback failed: "+str(exc))

    bitget=None
    try:
        bitget=collect_bitget()
        if bitget.get("data_quality",{}).get("core_timeframes_ok") and bitget.get("data_quality",{}).get("five_minute_data_fresh"):
            bitget["fallback_from"]="Binance USD-M Futures and Bybit linear Futures"
            bitget["prior_source_failures"]=failures
            return bitget
        bitget_errors={k:v.get("error") for k,v in bitget.get("timeframes",{}).items() if v.get("error")}
        failures.append("Bitget fallback incomplete: "+str(bitget_errors))
    except Exception as exc:
        failures.append("Bitget USDT-FUTURES fallback failed: "+str(exc))

    try:
        okx=collect_okx()
        if okx.get("data_quality",{}).get("core_timeframes_ok") and okx.get("data_quality",{}).get("five_minute_data_fresh"):
            okx["fallback_from"]="Binance USD-M Futures, Bybit linear Futures and Bitget USDT-FUTURES"
            okx["prior_source_failures"]=failures
            return okx
        okx_errors={k:v.get("error") for k,v in okx.get("timeframes",{}).items() if v.get("error")}
        failures.append("OKX fallback incomplete: "+str(okx_errors))
    except Exception as exc:
        failures.append("OKX BTC-USDT-SWAP fallback failed: "+str(exc))

    primary["data_quality"]["warnings"]=failures
    primary["fallback_attempts"]={
        "bybit":{"attempted":True,"core_timeframes_ok":bool(bybit and bybit.get("data_quality",{}).get("core_timeframes_ok"))},
        "bitget":{"attempted":True,"core_timeframes_ok":bool(bitget and bitget.get("data_quality",{}).get("core_timeframes_ok"))},
        "okx":{"attempted":True,"core_timeframes_ok":False}
    }
    return primary


if __name__ == "__main__":
    main()
