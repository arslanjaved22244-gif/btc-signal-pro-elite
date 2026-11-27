#!/usr/bin/env python3
"""Public-data institutional context layer. Read-only; no orders or API keys."""
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

MARKET_FILE = "market_data.json"
AI_FILE = "ai_result.json"
PAPER_FILE = "paper_trading.json"
METRICS_FILE = "advanced_metrics.json"
SIGNAL_FILE = "signal_result.json"
OUTPUT_FILE = "institutional_intelligence.json"
USER_AGENT = "BTC-Signal-Pro-Elite/6.0"


def load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def request_json(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def ema(values, period):
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [float(values[0])]
    for value in values[1:]:
        out.append(alpha * float(value) + (1.0 - alpha) * out[-1])
    return out


def atr_pct(rows, period=14):
    if len(rows) < period + 1:
        return None
    tr = []
    for previous, current in zip(rows[:-1], rows[1:]):
        tr.append(max(float(current["high"]) - float(current["low"]),
                      abs(float(current["high"]) - float(previous["close"])),
                      abs(float(current["low"]) - float(previous["close"]))))
    close = float(rows[-1]["close"])
    return (sum(tr[-period:]) / period / close * 100.0) if close else None


def market_regime(market):
    frames = market.get("timeframes", {})
    rows = frames.get("1h", {}).get("candles", [])
    fast_rows = frames.get("15m", {}).get("candles", [])
    if len(rows) < 205:
        return {"status": "INSUFFICIENT_DATA", "regime": "UNKNOWN",
                "reason": "At least 205 completed 1h Futures candles are required."}
    closes = [float(x["close"]) for x in rows]
    e50, e200 = ema(closes, 50), ema(closes, 200)
    slope = (e50[-1] / e50[-7] - 1.0) * 100 if e50[-7] else 0.0
    spread = (e50[-1] / e200[-1] - 1.0) * 100 if e200[-1] else 0.0
    vol = atr_pct(fast_rows) if len(fast_rows) >= 20 else atr_pct(rows)
    if vol is None:
        regime = "UNKNOWN"
    elif vol >= 0.45:
        regime = "HIGH_VOLATILITY"
    elif abs(spread) < 0.12 and abs(slope) < 0.035:
        regime = "RANGE_CHOP"
    elif spread > 0.12 and slope > 0.025:
        regime = "BULL_TREND"
    elif spread < -0.12 and slope < -0.025:
        regime = "BEAR_TREND"
    else:
        regime = "TRANSITION"
    return {"status": "AVAILABLE", "regime": regime, "ema50_200_spread_pct": round(spread, 4),
            "ema50_slope_6h_pct": round(slope, 4), "atr_pct": round(vol, 4) if vol is not None else None,
            "source_timeframe": "1h trend + 15m volatility", "note": "Rule-based regime classification, not a forecast."}


def volume_profile(market, buckets=48):
    rows = market.get("timeframes", {}).get("15m", {}).get("candles", [])
    if len(rows) < 40:
        return {"status": "INSUFFICIENT_DATA"}
    rows = rows[-min(400, len(rows)):]
    low = min(float(x["low"]) for x in rows)
    high = max(float(x["high"]) for x in rows)
    if high <= low:
        return {"status": "INSUFFICIENT_PRICE_RANGE"}
    step = (high - low) / buckets
    profile = [0.0] * buckets
    total = 0.0
    vwap_num = vwap_den = 0.0
    for row in rows:
        typical = (float(row["high"]) + float(row["low"]) + float(row["close"])) / 3.0
        volume = max(0.0, float(row.get("volume", 0) or 0))
        index = min(buckets - 1, max(0, int((typical - low) / step)))
        profile[index] += volume
        total += volume
        vwap_num += typical * volume
        vwap_den += volume
    poc_index = max(range(buckets), key=lambda i: profile[i])
    target = total * 0.70
    included = {poc_index}
    accumulated = profile[poc_index]
    left, right = poc_index - 1, poc_index + 1
    while accumulated < target and (left >= 0 or right < buckets):
        lv = profile[left] if left >= 0 else -1
        rv = profile[right] if right < buckets else -1
        if rv > lv:
            included.add(right); accumulated += max(0, rv); right += 1
        else:
            included.add(left); accumulated += max(0, lv); left -= 1
    vah = low + (max(included) + 1) * step
    val = low + min(included) * step
    price = float(rows[-1]["close"])
    return {"status": "AVAILABLE", "method": "15m typical-price volume bins; last 400 candles maximum",
            "poc": round(low + (poc_index + 0.5) * step, 2), "value_area_high": round(vah, 2),
            "value_area_low": round(val, 2), "vwap": round(vwap_num / vwap_den, 2) if vwap_den else None,
            "last_price": round(price, 2), "price_vs_vwap": "ABOVE" if vwap_den and price > vwap_num / vwap_den else "BELOW",
            "total_sampled_volume": round(total, 4), "note": "Estimated profile from candle volume, not exchange tick-level volume-at-price."}


def options_snapshot():
    url = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?" + urllib.parse.urlencode(
        {"currency": "BTC", "kind": "option"})
    try:
        payload = request_json(url)
        items = payload.get("result", [])
        calls = puts = call_volume = put_volume = 0.0
        call_iv, put_iv = [], []
        strikes = []
        for item in items:
            name = str(item.get("instrument_name", ""))
            parts = name.split("-")
            if len(parts) < 4:
                continue
            side = parts[-1].upper()
            try:
                strike = float(parts[-2])
            except ValueError:
                continue
            oi = float(item.get("open_interest", 0) or 0)
            vol = float(item.get("volume", 0) or 0)
            iv = float(item.get("mark_iv", 0) or 0)
            strikes.append((strike, side, oi))
            if side == "C":
                calls += oi; call_volume += vol
                if iv > 0: call_iv.append(iv)
            elif side == "P":
                puts += oi; put_volume += vol
                if iv > 0: put_iv.append(iv)
        ratio = puts / calls if calls else None
        spot = None
        try:
            ticker = request_json("https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd")
            spot = float(ticker.get("result", {}).get("index_price"))
        except Exception:
            pass
        max_pain = None
        if strikes:
            candidates = sorted({x[0] for x in strikes})
            def payout(settlement):
                return sum(oi * max(0.0, settlement - strike) if side == "C"
                           else oi * max(0.0, strike - settlement)
                           for strike, side, oi in strikes)
            max_pain = min(candidates, key=payout)
        return {"status": "AVAILABLE", "venue": "Deribit public BTC options",
                "put_call_open_interest_ratio": round(ratio, 4) if ratio is not None else None,
                "call_open_interest": round(calls, 2), "put_open_interest": round(puts, 2),
                "call_volume_24h": round(call_volume, 2), "put_volume_24h": round(put_volume, 2),
                "average_call_mark_iv_pct": round(sum(call_iv) / len(call_iv), 2) if call_iv else None,
                "average_put_mark_iv_pct": round(sum(put_iv) / len(put_iv), 2) if put_iv else None,
                "approx_max_pain_strike": max_pain, "btc_index_price": round(spot, 2) if spot else None,
                "instruments_sampled": len(items),
                "note": "Aggregated listed BTC options snapshot; max-pain is an approximation across returned instruments, not a price target."}
    except Exception as exc:
        return {"status": "UNAVAILABLE", "reason": str(exc)[:240], "venue": "Deribit public API"}


def yahoo_history(symbol, period_days=14):
    now = int(time.time())
    start = now - period_days * 86400
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(symbol, safe="") + "?" + urllib.parse.urlencode(
        {"period1": start, "period2": now, "interval": "1h", "events": "history"})
    payload = request_json(url)
    result = payload.get("chart", {}).get("result") or []
    if not result:
        raise ValueError("No Yahoo Finance chart result")
    item = result[0]
    timestamps = item.get("timestamp", [])
    quote = item.get("indicators", {}).get("quote", [{}])[0]
    closes = quote.get("close", [])
    values = {}
    for ts, close in zip(timestamps, closes):
        if close is not None:
            values[int(ts) // 3600] = float(close)
    return values


def pearson(a, b):
    pairs = [(a[k], b[k]) for k in a.keys() & b.keys() if a[k] > 0 and b[k] > 0]
    if len(pairs) < 12:
        return {"correlation": None, "samples": len(pairs), "status": "INSUFFICIENT_OVERLAP"}
    ra = [pairs[i][0] / pairs[i-1][0] - 1 for i in range(1, len(pairs))]
    rb = [pairs[i][1] / pairs[i-1][1] - 1 for i in range(1, len(pairs))]
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    va = sum((x-ma)**2 for x in ra); vb = sum((x-mb)**2 for x in rb)
    corr = sum((x-ma)*(y-mb) for x, y in zip(ra, rb)) / math.sqrt(va*vb) if va and vb else None
    return {"correlation": round(corr, 3) if corr is not None else None,
            "samples": len(ra), "status": "AVAILABLE" if corr is not None else "CONSTANT_SERIES"}


def macro_correlations():
    symbols = {"NASDAQ": "^IXIC", "DXY": "DX-Y.NYB", "GOLD": "GC=F"}
    try:
        btc = yahoo_history("BTC-USD")
        output = {}
        for name, symbol in symbols.items():
            try:
                output[name] = pearson(btc, yahoo_history(symbol))
            except Exception as exc:
                output[name] = {"status": "UNAVAILABLE", "reason": str(exc)[:180]}
        return {"status": "PARTIAL" if any(x.get("correlation") is None for x in output.values()) else "AVAILABLE",
                "window": "14 days of hourly returns", "correlations_vs_btc": output,
                "note": "Yahoo Finance hourly proxy correlations; asynchronous market hours and feed gaps can reduce comparability."}
    except Exception as exc:
        return {"status": "UNAVAILABLE", "reason": str(exc)[:220], "correlations_vs_btc": {}}


def false_breakout_and_sweep(market):
    rows = market.get("timeframes", {}).get("15m", {}).get("candles", [])
    if len(rows) < 25:
        return {"status": "INSUFFICIENT_DATA"}
    recent = rows[-1]
    prior = rows[-21:-1]
    ph = max(float(x["high"]) for x in prior)
    pl = min(float(x["low"]) for x in prior)
    high, low, close = map(float, (recent["high"], recent["low"], recent["close"]))
    if low < pl and close > pl:
        return {"status": "BULLISH_SWEEP_RECLAIM", "level": round(pl, 2), "wick": round(low, 2),
                "note": "Latest completed 15m candle swept the prior 20-bar low and reclaimed it."}
    if high > ph and close < ph:
        return {"status": "BEARISH_SWEEP_REJECTION", "level": round(ph, 2), "wick": round(high, 2),
                "note": "Latest completed 15m candle swept the prior 20-bar high and closed back below."}
    if close > ph:
        return {"status": "BREAKOUT_ABOVE_PRIOR_RANGE", "level": round(ph, 2), "note": "Close is above prior 20-bar high; retest/volume confirmation still required."}
    if close < pl:
        return {"status": "BREAKDOWN_BELOW_PRIOR_RANGE", "level": round(pl, 2), "note": "Close is below prior 20-bar low; retest/volume confirmation still required."}
    return {"status": "NO_CONFIRMED_SWEEP_OR_BREAKOUT"}


def scenario_forecasts(market, regime):
    output = {}
    for horizon, tf, count in (("15m", "5m", 3), ("30m", "5m", 6), ("1h", "15m", 4)):
        rows = market.get("timeframes", {}).get(tf, {}).get("candles", [])
        if len(rows) < 60:
            output[horizon] = {"status": "INSUFFICIENT_DATA", "scenario": "WAIT"}
            continue
        closes = [float(x["close"]) for x in rows]
        e9, e21, e50 = ema(closes, 9), ema(closes, 21), ema(closes, 50)
        px = closes[-1]
        momentum = (px / closes[-min(len(closes), count+1)] - 1) * 100 if closes[-min(len(closes), count+1)] else 0
        spread = (e9[-1] - e21[-1]) / px * 10000
        trend = (e21[-1] - e50[-1]) / px * 10000
        score = max(-100, min(100, 0.45 * spread + 0.30 * trend + 0.25 * momentum * 10))
        scenario = "LONG_BIAS" if score >= 2.5 else "SHORT_BIAS" if score <= -2.5 else "RANGE_OR_WAIT"
        vol = atr_pct(rows)
        output[horizon] = {"status": "RESEARCH_ONLY", "scenario": scenario,
                           "directional_score": round(score, 2), "recent_momentum_pct": round(momentum, 4),
                           "ema_spread_bps": round(spread, 2), "atr_pct": round(vol, 4) if vol is not None else None,
                           "regime": regime.get("regime", "UNKNOWN"),
                           "note": "Heuristic scenario, not a calibrated probability or an entry signal."}
    return output



def _parse_utc(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _age_minutes(value, now):
    stamp = _parse_utc(value)
    if stamp is None:
        return None
    age = (now - stamp).total_seconds() / 60
    return round(age, 2) if age >= -1 else None


def feed_health(market, news, liquidation, comparison, now=None):
    """Eleven-point source audit. Availability is never presented as proof of accuracy."""
    now = now or datetime.now(timezone.utc)
    tf = market.get("timeframes", {})
    feeds = market.get("feeds", {})
    generated_age = _age_minutes(market.get("generated_at"), now)
    five = tf.get("5m", {})
    five_age = five.get("age_seconds")
    core = all(tf.get(name, {}).get("ok") is True for name in ("5m", "15m", "1h", "4h", "1d"))
    oi = feeds.get("open_interest_history_5m", {})
    oi_rows = oi.get("data", []) if oi.get("ok") else []
    oi_stamp = None
    if isinstance(oi_rows, list) and oi_rows:
        ages = []
        for row in oi_rows:
            raw_stamp = row.get("timestamp") if isinstance(row, dict) else None
            try:
                raw_stamp = int(raw_stamp)
                if raw_stamp < 10_000_000_000:
                    raw_stamp *= 1000
                oi_age = (now.timestamp() * 1000 - raw_stamp) / 60000
                if -1 <= oi_age <= 1e6:
                    ages.append(oi_age)
            except (TypeError, ValueError):
                continue
        # Exchange APIs differ in ascending/descending order; use the newest timestamp.
        if ages:
            oi_stamp = round(min(ages), 2)
    # Some regional exchange fallbacks expose a current OI snapshot but not
    # historical OI rows. Count it only when the parent market snapshot itself
    # is timestamped and fresh; never describe this as an OI history series.
    oi_current = feeds.get("open_interest_current", {})
    oi_current_ok = (
        oi_stamp is None and oi_current.get("ok") is True
        and isinstance(oi_current.get("data"), dict)
        and oi_current.get("data")
        and generated_age is not None and 0 <= generated_age <= 15
    )
    oi_ok = (bool(oi.get("ok")) and bool(oi_rows) and oi_stamp is not None and oi_stamp <= 15) or oi_current_ok
    oi_detail = (
        f"historical OI latest row age {oi_stamp}m" if oi_stamp is not None
        else "current OI snapshot; historical series unavailable" if oi_current_ok
        else "timestamp unavailable"
    )
    derived = market.get("derived", {})
    has_visible_depth = (
        derived.get("visible_depth_bid_notional_usdt") is not None
        and derived.get("visible_depth_ask_notional_usdt") is not None
        and generated_age is not None and 0 <= generated_age <= 2
    )
    order_book_ok = feeds.get("order_book", {}).get("ok") is True or has_visible_depth
    taker = feeds.get("taker_flow_5m", {})
    taker_rows = taker.get("data", []) if taker.get("ok") else []
    news_age = _age_minutes(news.get("generated_at"), now)
    compare_age = _age_minutes(comparison.get("generated_at"), now)
    venues = comparison.get("venues", {})
    venue_count = sum(1 for item in venues.values() if isinstance(item, dict) and item.get("available") is True)
    last_liq_age = _age_minutes(liquidation.get("last_message_at"), now)
    trump_sources = news.get("sources", [])
    checks = [
        ("5m_futures_candles", bool(five.get("ok")) and isinstance(five_age, (int, float)) and five_age <= 600,
         f"candle age {five_age}s" if five_age is not None else "age unavailable"),
        ("all_five_timeframes", core, "5m / 15m / 1h / 4h / 1d"),
        ("funding_snapshot", bool(feeds.get("mark_price_funding", {}).get("ok")), "public mark-price/funding endpoint"),
        ("open_interest_data", oi_ok, oi_detail),
        ("executed_taker_flow", bool(taker.get("ok")) and isinstance(taker_rows, list) and bool(taker_rows),
         "exchange taker buy/sell volume feed; not account positioning"),
        ("rolling_cvd", market.get("derived", {}).get("cvd_base") is not None and int(market.get("derived", {}).get("trade_count", 0) or 0) > 0,
         "latest aggregate trades only; not exchange-wide historical CVD"),
        ("order_book_depth", order_book_ok,
         "visible public depth snapshot" if feeds.get("order_book", {}).get("ok") else
         f"derived top-20 depth snapshot; market age {generated_age}m" if has_visible_depth else
         "depth snapshot unavailable or stale"),
        ("fresh_news_scan", news.get("available") is True and news_age is not None and news_age <= 15,
         f"news snapshot age {news_age}m" if news_age is not None else "timestamp unavailable"),
        ("trump_public_post_archive", any("trump" in str(x).lower() for x in trump_sources),
         "unofficial third-party RSS archive; may lag or omit posts"),
        ("cross_exchange_perpetuals", venue_count >= 2 and compare_age is not None and compare_age <= 15,
         f"{venue_count} venues available; snapshot age {compare_age}m" if compare_age is not None else f"{venue_count} venues; timestamp unavailable"),
        ("continuous_liquidation_stream", liquidation.get("continuous") is True and liquidation.get("connected") is True and last_liq_age is not None and last_liq_age <= 2,
         f"last WebSocket event/message age {last_liq_age}m" if last_liq_age is not None else "not connected / no recent message")
    ]
    items = [{"name": name, "passed": bool(passed), "detail": detail} for name, passed, detail in checks]
    return {
        "status": "ALL_AVAILABLE" if all(x["passed"] for x in items) else "PARTIAL",
        "passed": sum(x["passed"] for x in items),
        "total": len(items),
        "items": items,
        "market_snapshot_age_minutes": generated_age,
        "note": "A feed marked unavailable is not guessed or replaced. This audit reports source presence and freshness, not signal accuracy. GitHub Actions sampled liquidation data does not count as a continuous stream."
    }


def quality_gate(ai, paper, metrics):
    validation = ai.get("validation_gate", {})
    walk = ai.get("walk_forward_metrics", {})
    shadow = paper.get("shadow_evaluation", {})
    backtest = metrics.get("backtest_5m", {})
    checks = {
        "ai_validation_passed": validation.get("passed") is True,
        "model_beats_majority_baseline": (
            walk.get("accuracy") is not None and walk.get("majority_class_baseline_pct") is not None
            and float(walk["accuracy"]) > float(walk["majority_class_baseline_pct"])),
        "macro_f1_at_least_30": walk.get("macro_f1") is not None and float(walk["macro_f1"]) >= 30.0,
        "shadow_sample_at_least_30": int(shadow.get("closed_forecasts", 0) or 0) >= 30,
        "shadow_beats_majority_baseline": shadow.get("beats_majority_baseline") is True,
        "shadow_validation_passed": shadow.get("validation_gate_passed_now") is True,
        "cost_aware_backtest_completed": backtest.get("status") == "COMPLETED" and int(backtest.get("total_candles", 0) or 0) >= 300,
        "cost_aware_backtest_positive": backtest.get("net_return_pct") is not None and float(backtest["net_return_pct"]) > 0
    }
    passed = all(checks.values())
    return {"status": "PASSED" if passed else "BLOCKED", "passed": passed, "checks": checks,
            "passed_checks": sum(checks.values()), "total_checks": len(checks),
            "policy": "Directional signals remain blocked until AI validation beats the majority baseline, macro-F1 is >=30, shadow sample >=30, beats its own majority-class baseline and passes its validation, and a >=300-candle cost-aware holdout backtest is positive.",
            "note": "This is a conservative evidence gate, not proof of future profitability."}


def build():
    market, ai, paper, metrics, signal = (load(p) for p in
        (MARKET_FILE, AI_FILE, PAPER_FILE, METRICS_FILE, SIGNAL_FILE))
    news = load("event_guard.json")
    liquidation = load("liquidation_data.json")
    comparison = load("exchange_comparison.json")
    regime = market_regime(market)
    return {
        "system": "BTC Signal Pro Elite Institutional Intelligence",
        "version": "1.0-six-upgrades",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market_regime": regime,
        "volume_profile": volume_profile(market),
        "liquidity_structure": false_breakout_and_sweep(market),
        "options": options_snapshot(),
        "macro_correlations": macro_correlations(),
        "next_move_scenarios": scenario_forecasts(market, regime),
        "signal_quality_gate": quality_gate(ai, paper, metrics),
        "shadow_evaluation": paper.get("shadow_evaluation", {}),
        "feed_health": feed_health(market, news, liquidation, comparison),
        "execution": "MANUAL_ONLY_NO_ORDERS",
        "disclaimer": "Public-data research context only. Scenarios are not calibrated probabilities; unavailable feeds are explicitly marked."
    }


def main():
    result = build()
    with open(OUTPUT_FILE, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps({"status": result["signal_quality_gate"]["status"],
                      "checks": result["signal_quality_gate"]["passed_checks"],
                      "options": result["options"].get("status"),
                      "macro": result["macro_correlations"].get("status"),
                      "regime": result["market_regime"].get("regime")}, indent=2))


if __name__ == "__main__":
    main()
