#!/usr/bin/env python3
"""Hybrid BTC Futures confluence engine. Signal-only; never places orders."""
import json
import math
from datetime import datetime, timezone

MARKET_FILE = "market_data.json"
MODEL_FILE = "ai_result.json"
OUTPUT_FILE = "signal_result.json"
NEWS_FILE = "event_guard.json"
TIMEFRAMES = ("5m", "15m", "1h", "4h", "1d")


def load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def ema(values, period):
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out


def rsi(values, period=14):
    if len(values) <= period:
        return 50.0
    changes = [b - a for a, b in zip(values[:-1], values[1:])]
    gain = sum(max(x, 0) for x in changes[:period]) / period
    loss = sum(max(-x, 0) for x in changes[:period]) / period
    for change in changes[period:]:
        gain = (gain * (period - 1) + max(change, 0)) / period
        loss = (loss * (period - 1) + max(-change, 0)) / period
    if loss == 0:
        return 100.0 if gain else 50.0
    return 100 - 100 / (1 + gain / loss)


def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    tr = []
    for prev, cur in zip(candles[:-1], candles[1:]):
        tr.append(max(cur["high"] - cur["low"],
                      abs(cur["high"] - prev["close"]),
                      abs(cur["low"] - prev["close"])))
    return sum(tr[-period:]) / period


def macd(values):
    def calc(period):
        alpha=2/(period+1); out=[values[0]]
        for v in values[1:]: out.append(alpha*v+(1-alpha)*out[-1])
        return out
    if len(values)<35: return None
    line=[a-b for a,b in zip(calc(12),calc(26))]
    alpha=2/10; sig=[line[0]]
    for v in line[1:]: sig.append(alpha*v+(1-alpha)*sig[-1])
    return {"line":line[-1],"signal":sig[-1],"histogram":line[-1]-sig[-1]}

def timeframe_analysis(item):
    if not item.get("ok"):
        return {"available": False, "bias": "UNKNOWN"}
    rows = item.get("candles", [])
    closes = [float(x["close"]) for x in rows]
    if len(closes) < 205:
        return {"available": False, "bias": "UNKNOWN"}
    e50, e200 = ema(closes, 50)[-1], ema(closes, 200)[-1]
    r = rsi(closes)
    price = closes[-1]
    bias = "BULLISH" if price > e50 > e200 else "BEARISH" if price < e50 < e200 else "NEUTRAL"
    return {
        "available": True, "bias": bias, "price": round(price, 2),
        "ema50": round(e50, 2), "ema200": round(e200, 2),
        "rsi14": round(r, 2), "atr14": round(atr(rows) or 0, 2),
        "macd": {k: round(v, 5) for k,v in (macd(closes) or {}).items()},
        "age_seconds": item.get("age_seconds")
    }


def feed_data(market, key):
    feed = market.get("feeds", {}).get(key, {})
    return feed.get("data") if feed.get("ok") else None


def run():
    market, model, news = load(MARKET_FILE), load(MODEL_FILE), load(NEWS_FILE)
    quality = market.get("data_quality", {})
    analyses = {tf: timeframe_analysis(market.get("timeframes", {}).get(tf, {})) for tf in TIMEFRAMES}
    warnings = list(quality.get("warnings", []))
    derived = market.get("derived", {})
    premium = feed_data(market, "mark_price_funding") or {}
    oi_hist = feed_data(market, "open_interest_history_5m") or []
    taker_hist = feed_data(market, "taker_flow_5m") or []

    votes = {"LONG": 0.0, "SHORT": 0.0}
    weights = {"5m": 1.0, "15m": 1.5, "1h": 2.0, "4h": 2.0, "1d": 1.0}
    for tf, item in analyses.items():
        if item.get("available"):
            if item["bias"] == "BULLISH":
                votes["LONG"] += weights[tf]
            elif item["bias"] == "BEARISH":
                votes["SHORT"] += weights[tf]

    # Order-flow confluence: rolling CVD and top-100 visible book depth.
    cvd = derived.get("cvd_base")
    imbalance = derived.get("order_book_imbalance")
    if isinstance(cvd, (int, float)):
        if cvd > 0:
            votes["LONG"] += 1.0
        elif cvd < 0:
            votes["SHORT"] += 1.0
    if isinstance(imbalance, (int, float)):
        if imbalance > 0.12:
            votes["LONG"] += 0.75
        elif imbalance < -0.12:
            votes["SHORT"] += 0.75

    # Funding is a crowding warning, not a standalone directional trigger.
    funding = float(premium.get("lastFundingRate", 0) or 0)
    funding_pct = funding * 100
    crowding = "NEUTRAL"
    if funding_pct >= 0.03:
        crowding = "LONGS_CROWDED"
        votes["SHORT"] += 0.5
    elif funding_pct <= -0.03:
        crowding = "SHORTS_CROWDED"
        votes["LONG"] += 0.5

    oi_change_pct = None
    if isinstance(oi_hist, list) and len(oi_hist) >= 2:
        first = float(oi_hist[0].get("sumOpenInterest", 0) or 0)
        last = float(oi_hist[-1].get("sumOpenInterest", 0) or 0)
        if first > 0:
            oi_change_pct = (last / first - 1) * 100
    taker_ratio = None
    if isinstance(taker_hist, list) and taker_hist:
        try:
            taker_ratio = float(taker_hist[-1].get("buySellRatio"))
        except (TypeError, ValueError):
            pass

    # A high OI expansion amplifies the current directional flow; OI alone
    # is not directional. Do not assign a side without price/flow confirmation.
    if oi_change_pct is not None and oi_change_pct > 1.0:
        if isinstance(cvd, (int, float)) and cvd > 0:
            votes["LONG"] += 0.5
        elif isinstance(cvd, (int, float)) and cvd < 0:
            votes["SHORT"] += 0.5
    if taker_ratio is not None:
        if taker_ratio > 1.15:
            votes["LONG"] += 0.5
        elif taker_ratio < 0.87:
            votes["SHORT"] += 0.5

    available_weight = sum(weights[tf] for tf, a in analyses.items() if a.get("available"))
    max_weight = sum(weights.values()) + 2.75
    long_score = votes["LONG"]
    short_score = votes["SHORT"]
    total = long_score + short_score
    confidence = (max(long_score, short_score) / max_weight * 100) if max_weight else 0
    margin = abs(long_score - short_score)

    signal = "WAIT"
    if quality.get("status") == "HEALTHY" and quality.get("five_minute_data_fresh"):
        if total > 0 and margin >= 2.0 and confidence >= 42:
            signal = "LONG" if long_score > short_score else "SHORT"

    # A spot-trained ML model cannot be treated as a Futures confirmation.
    model_source = str(model.get("data_source", ""))
    model_is_futures = "Futures" in model_source and "Spot" not in model_source
    model_scores = model.get("class_scores", {})
    ml_bias = "WAIT"
    if model_is_futures:
        ml_bias = max(("SHORT", "WAIT", "LONG"), key=lambda x: float(model_scores.get(x, 0) or 0))
    if signal != "WAIT" and model_is_futures and ml_bias not in (signal, "WAIT"):
        warnings.append("Futures ML model disagrees with confluence direction; signal downgraded to WAIT.")
        signal = "WAIT"
    elif signal != "WAIT" and not model_is_futures:
        warnings.append("ML model uses a non-Futures or unknown source; it is not used as confirmation.")

    # Require the Futures ML model to agree directionally; disagreement or WAIT means no entry.
    if signal != "WAIT" and model_is_futures and ml_bias != signal:
        signal = "WAIT"
        warnings.append("Four-model Futures ML vote does not confirm the confluence direction; WAIT enforced.")
    if news.get("block"):
        signal = "WAIT"
        warnings.append("High-risk news lock active; entries blocked until the event lock expires.")
    elif not news.get("available"):
        warnings.append("News monitor unavailable; verify major headlines manually before any trade.")
    # Volatility circuit breaker: block extreme 5m ATR or an unusually large latest candle.
    five = market.get("timeframes", {}).get("5m", {}).get("candles", [])
    atr_pct = (float(atr_value) / float(price) * 100.0) if price and atr_value else 0.0
    range_spike = False
    if len(five) >= 2 and atr_value:
        latest_candle = five[-1]
        range_spike = (float(latest_candle.get("high", 0))-float(latest_candle.get("low", 0))) > 2.5*float(atr_value)
    volatility_block = atr_pct >= 0.45 or range_spike
    if volatility_block:
        signal = "WAIT"
        warnings.append("Volatility circuit breaker active: 5m ATR or candle range exceeded safety threshold.")

    price = analyses.get("5m", {}).get("price")
    atr_value = analyses.get("5m", {}).get("atr14")
    risk = {"entry_reference": price, "stop_loss": None, "take_profit_1": None,
            "take_profit_2": None, "risk_reward": "1:1.5 and 1:2.5", "method": "1.5x 5m ATR stop"}
    if signal in ("LONG", "SHORT") and price and atr_value and atr_value > 0:
        distance = 1.5 * atr_value
        if signal == "LONG":
            risk.update({"stop_loss": round(price - distance, 2),
                         "take_profit_1": round(price + distance * 1.5, 2),
                         "take_profit_2": round(price + distance * 2.5, 2)})
        else:
            risk.update({"stop_loss": round(price + distance, 2),
                         "take_profit_1": round(price - distance * 1.5, 2),
                         "take_profit_2": round(price - distance * 2.5, 2)})
    else:
        risk["method"] = "No levels while signal is WAIT or ATR unavailable"

    return {
        "system": "BTC Signal Pro Elite Hybrid Confluence Engine",
        "version": "4.1-hybrid-futures-confluence",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": "BTCUSDT", "market_type": "BINANCE_USDM_FUTURES",
        "signal": signal,
        "confidence_score": round(confidence, 2),
        "scores": {"LONG": round(long_score, 2), "SHORT": round(short_score, 2)},
        "timeframe_analysis": analyses,
        "order_flow": {"cvd_base": cvd, "order_book_imbalance": imbalance,
                       "funding_rate_pct": round(funding_pct, 6), "funding_crowding": crowding,
                       "open_interest_change_30m_pct": round(oi_change_pct, 4) if oi_change_pct is not None else None,
                       "taker_buy_sell_ratio": taker_ratio},
        "ml_context": {"model_version": model.get("model_version"),
                       "data_source": model_source, "futures_source": model_is_futures,
                       "signal": ml_bias},
        "risk_levels": risk,
        "data_quality": quality,
        "news_guard": news,
        "volatility_guard": {"blocked": volatility_block, "atr_5m_pct": round(atr_pct, 4), "atr_threshold_pct": 0.45, "range_spike": range_spike},
        "warnings": warnings,
        "execution": "MANUAL_ONLY_NO_ORDERS"
    }


if __name__ == "__main__":
    result = run()
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(json.dumps({"signal": result["signal"], "confidence": result["confidence_score"],
                      "scores": result["scores"], "warnings": result["warnings"]}, indent=2))
