#!/usr/bin/env python3
"""Hybrid BTC Futures confluence engine. Signal-only; never places orders.
Institutional intelligence quality gate is mandatory for directional authorization."""
import json
import math
from datetime import datetime, timezone

MARKET_FILE = "market_data.json"
MODEL_FILE = "ai_result.json"
OUTPUT_FILE = "signal_result.json"
NEWS_FILE = "event_guard.json"
INTELLIGENCE_FILE = "institutional_intelligence.json"
TIMEFRAMES = ("5m", "15m", "1h", "4h", "1d")


def load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def fresh_5m_derivative_rows(rows, now_ms=None, max_age_minutes=15):
    """Return only timestamped 5m derivative observations within the freshness window."""
    if not isinstance(rows, list) or not rows:
        return []
    now_ms = int(now_ms if now_ms is not None else datetime.now(timezone.utc).timestamp() * 1000)
    fresh = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            stamp = int(row.get("timestamp"))
            if stamp < 10_000_000_000:
                stamp *= 1000
        except (TypeError, ValueError):
            continue
        age_ms = now_ms - stamp
        if -60_000 <= age_ms <= max_age_minutes * 60_000:
            fresh.append(row)
    return fresh


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
    macd_value = macd(closes) or {}
    histogram = float(macd_value.get("histogram", 0.0))
    # Faster momentum context complements (but does not replace) EMA structure.
    momentum_bias = (
        "BULLISH" if price > e50 and histogram > 0 else
        "BEARISH" if price < e50 and histogram < 0 else "NEUTRAL"
    )
    return {
        "available": True, "bias": bias, "momentum_bias": momentum_bias,
        "price": round(price, 2),
        "ema50": round(e50, 2), "ema200": round(e200, 2),
        "rsi14": round(r, 2), "atr14": round(atr(rows) or 0, 2),
        "macd": {k: round(v, 5) for k,v in macd_value.items()},
        "age_seconds": item.get("age_seconds")
    }


def feed_data(market, key):
    feed = market.get("feeds", {}).get(key, {})
    return feed.get("data") if feed.get("ok") else None


MAX_MODEL_MARKET_BASIS_PCT = 0.25
MAX_MODEL_CANDLE_SKEW_MINUTES = 15


def validate_model_market_alignment(model, market):
    """Require the model's actual 5m perpetual venue, price and candle time to align."""
    model_source = str(model.get("data_source_by_timeframe", {}).get("5m")
                       or model.get("data_source", ""))
    market_exchange = str(market.get("exchange", ""))
    model_source_lower = model_source.lower()
    market_exchange_lower = market_exchange.lower()
    model_is_futures = "futures" in model_source_lower and "spot" not in model_source_lower
    market_is_futures = market.get("data_quality", {}).get("futures_only") is True
    if not model_is_futures or not market_is_futures:
        return {"passed": False, "type": "BLOCKED",
                "reason": "Both model 5m input and live market must be verified perpetual Futures."}
    ai_symbol = str(model.get("symbol", "BTC/USDT")).upper().replace("/", "").replace("-", "")
    market_symbol = str(market.get("symbol", "BTCUSDT")).upper().replace("/", "").replace("-", "")
    if "BTC" not in ai_symbol or "USDT" not in ai_symbol or "BTC" not in market_symbol or "USDT" not in market_symbol:
        return {"passed": False, "type": "BLOCKED",
                "reason": "Model alignment is restricted to BTC/USDT perpetual Futures."}
    rows = market.get("timeframes", {}).get("5m", {}).get("candles", [])
    try:
        market_price = float(rows[-1]["close"])
        model_price = float(model.get("last_price"))
        market_time = int(rows[-1]["time"])
        model_time = int(model.get("last_candle_time"))
        if min(market_price, model_price, market_time, model_time) <= 0:
            raise ValueError("invalid alignment inputs")
        basis_pct = abs(model_price / market_price - 1.0) * 100.0
        skew_minutes = abs(model_time - market_time) / 60000.0
    except (IndexError, KeyError, TypeError, ValueError):
        return {"passed": False, "type": "BLOCKED",
                "reason": "Alignment requires valid current prices and 5m candle timestamps."}
    same_venue = bool(market_exchange_lower and market_exchange_lower in model_source_lower)
    passed = basis_pct <= MAX_MODEL_MARKET_BASIS_PCT and skew_minutes <= MAX_MODEL_CANDLE_SKEW_MINUTES
    alignment_type = "SAME_VENUE" if same_venue and passed else (
        "CROSS_VENUE_FUTURES" if passed else "BLOCKED"
    )
    return {
        "passed": passed, "type": alignment_type,
        "model_venue": model_source, "market_venue": market_exchange or "UNKNOWN",
        "basis_pct": round(basis_pct, 5),
        "candle_skew_minutes": round(skew_minutes, 2),
        "max_basis_pct": MAX_MODEL_MARKET_BASIS_PCT,
        "max_candle_skew_minutes": MAX_MODEL_CANDLE_SKEW_MINUTES,
        "reason": ("Same-venue or cross-venue perpetual prices and 5m candle times are aligned."
                   if passed else "Perpetual price basis or 5m candle-time skew exceeds safety tolerance.")
    }

def detect_recent_liquidity_sweep(market):
    """Detect a confirmed wick-and-reclaim on recent closed 15m Futures candles."""
    item = market.get("timeframes", {}).get("15m", {})
    candles = item.get("candles", []) if item.get("ok") else []
    if len(candles) < 25:
        return {"direction": "NONE", "status": "INSUFFICIENT_15M_HISTORY"}

    # Ignore a potentially still-forming final candle when feed timestamps are current.
    # The data engine publishes completed candles, but keep the detector robust to feeds
    # that include the live candle by checking its open timestamp against UTC now.
    import time
    now_ms = int(time.time() * 1000)
    if candles and int(candles[-1].get("time", 0)) + 15 * 60 * 1000 > now_ms:
        candles = candles[:-1]
    if len(candles) < 25:
        return {"direction": "NONE", "status": "INSUFFICIENT_CLOSED_CANDLES"}

    # Check the most recent eight completed bars; each candidate is compared with
    # the 20 bars immediately before it, avoiding look-ahead.
    for index in range(len(candles) - 1, max(19, len(candles) - 9), -1):
        candle = candles[index]
        prior = candles[index - 20:index]
        prior_high = max(float(x["high"]) for x in prior)
        prior_low = min(float(x["low"]) for x in prior)
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        avg_range = sum(float(x["high"]) - float(x["low"]) for x in prior[-14:]) / min(14, len(prior))
        min_wick = max(avg_range * 0.08, close * 0.00015)
        if low < prior_low - min_wick and close > prior_low:
            return {
                "direction": "LONG", "status": "CONFIRMED_BULLISH_SWEEP",
                "level": round(prior_low, 2), "wick_extreme": round(low, 2),
                "close": round(close, 2), "time": int(candle.get("time", 0)),
                "bars_ago": len(candles) - 1 - index,
                "note": "15m wick swept below the prior 20-bar low and closed back above it."
            }
        if high > prior_high + min_wick and close < prior_high:
            return {
                "direction": "SHORT", "status": "CONFIRMED_BEARISH_SWEEP",
                "level": round(prior_high, 2), "wick_extreme": round(high, 2),
                "close": round(close, 2), "time": int(candle.get("time", 0)),
                "bars_ago": len(candles) - 1 - index,
                "note": "15m wick swept above the prior 20-bar high and closed back below it."
            }
    return {"direction": "NONE", "status": "NO_CONFIRMED_SWEEP"}


def run():
    market, model, news = load(MARKET_FILE), load(MODEL_FILE), load(NEWS_FILE)
    institutional = load(INTELLIGENCE_FILE)
    quality_gate = institutional.get("signal_quality_gate", {})
    quality = market.get("data_quality", {})
    analyses = {tf: timeframe_analysis(market.get("timeframes", {}).get(tf, {})) for tf in TIMEFRAMES}
    warnings = list(quality.get("warnings", []))
    derived = market.get("derived", {})
    premium = feed_data(market, "mark_price_funding") or {}
    oi_hist_raw = feed_data(market, "open_interest_history_5m") or []
    taker_hist_raw = feed_data(market, "taker_flow_5m") or []
    oi_hist = fresh_5m_derivative_rows(oi_hist_raw)
    taker_hist = fresh_5m_derivative_rows(taker_hist_raw)
    if oi_hist_raw and not oi_hist:
        warnings.append("Open-interest history is stale or missing timestamps; excluded from signal scoring.")
    if taker_hist_raw and not taker_hist:
        warnings.append("Taker-flow history is stale or missing timestamps; excluded from signal scoring.")
    liquidity_sweep = detect_recent_liquidity_sweep(market)

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

    # A confirmed 15m wick-and-reclaim adds modest reversal evidence; it never
    # overrides the Futures ML agreement, news lock, volatility breaker or score gate.
    if liquidity_sweep.get("direction") == "LONG":
        votes["LONG"] += 1.25
    elif liquidity_sweep.get("direction") == "SHORT":
        votes["SHORT"] += 1.25

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
    max_weight = sum(weights.values()) + 4.0
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
    model_source_is_futures = "Futures" in model_source and "Spot" not in model_source
    model_validation_passed = model.get("validation_gate", {}).get("passed") is True
    # Same-venue Futures are compared directly; cross-venue model outputs must
    # also pass strict price-basis and candle-time alignment checks.
    model_market_alignment = validate_model_market_alignment(model, market)
    model_is_futures = (
        model_source_is_futures and model_validation_passed
        and model_market_alignment.get("passed") is True
    )
    if model_source_is_futures and not model_market_alignment.get("passed"):
        warnings.append("Cross-venue AI authorization blocked: " + str(model_market_alignment.get("reason", "market alignment failed")))
    model_scores = model.get("class_scores", {})
    ml_bias = "WAIT"
    if model_is_futures:
        ml_bias = max(("SHORT", "WAIT", "LONG"), key=lambda x: float(model_scores.get(x, 0) or 0))
    if signal != "WAIT" and model_is_futures and ml_bias not in (signal, "WAIT"):
        warnings.append("Futures ML model disagrees with confluence direction; signal downgraded to WAIT.")
        signal = "WAIT"
    elif signal != "WAIT" and not model_is_futures:
        signal = "WAIT"
        warnings.append("Directional entries blocked: AI must use verified perpetual Futures data AND pass the global walk-forward validation gate. Spot, unknown-source or failed-validation ML cannot authorize a Futures trade.")

    # Require the Futures ML model to agree directionally; disagreement or WAIT means no entry.
    if signal != "WAIT" and model_is_futures and ml_bias != signal:
        signal = "WAIT"
        warnings.append("Four-model Futures ML vote does not confirm the confluence direction; WAIT enforced.")
    if news.get("block"):
        signal = "WAIT"
        warnings.append("High-risk news lock active; entries blocked until the event lock expires.")
    elif not news.get("available"):
        # Missing headlines are not proof of an active event. Keep the signal
        # conservative by demanding stronger independent price/flow confluence.
        if signal != "WAIT" and (confidence < 55 or margin < 3.5):
            signal = "WAIT"
            warnings.append("News monitor unavailable and confluence is not strong enough for the elevated safety threshold; WAIT.")
        else:
            warnings.append("News monitor unavailable; directional signal allowed only with elevated confluence. Check breaking news manually before entry.")
    price = analyses.get("5m", {}).get("price")
    atr_value = analyses.get("5m", {}).get("atr14")

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

    # Additional evidence gate: no directional signal until the full six-part
    # intelligence layer confirms validation, shadow sample and cost-aware backtest.
    if quality_gate.get("passed") is not True:
        if signal in ("LONG", "SHORT"):
            signal = "WAIT"
        warnings.append("Institutional quality gate blocked: directional entries remain disabled until AI baseline, macro-F1, 30+ shadow forecasts and positive 300+ candle cost-aware backtest all pass.")

    # A separate early-setup readout makes developing direction visible without
    # mislabeling a weak setup as an actionable LONG/SHORT entry.
    momentum_weights = {"5m": 1.0, "15m": 2.0, "1h": 1.5, "4h": 1.5, "1d": 0.5}
    momentum_scores = {"LONG": 0.0, "SHORT": 0.0}
    for tf, item in analyses.items():
        if item.get("available"):
            direction = item.get("momentum_bias")
            if direction == "BULLISH":
                momentum_scores["LONG"] += momentum_weights[tf]
            elif direction == "BEARISH":
                momentum_scores["SHORT"] += momentum_weights[tf]
    momentum_margin = abs(momentum_scores["LONG"] - momentum_scores["SHORT"])
    momentum_leader = max(momentum_scores, key=momentum_scores.get)
    watch_signal = "WAIT"
    watch_status = "NO_CLEAR_EARLY_SETUP"
    if (quality.get("status") == "HEALTHY" and quality.get("five_minute_data_fresh")
            and not news.get("block") and not volatility_block
            and max(momentum_scores.values()) >= 3.5 and momentum_margin >= 1.5):
        watch_signal = momentum_leader
        watch_status = "EARLY_SETUP_ONLY_NOT_ENTRY"
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
        "version": "4.3-hybrid-sweep-validated-futures",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": "BTCUSDT",
        "market_type": market.get("market_type", "UNKNOWN"),
        "market_exchange": market.get("exchange", "Binance" if "BINANCE" in str(market.get("market_type", "")) else "Unknown"),
        "market_instrument": market.get("symbol", "BTCUSDT"),
        "market_source": market.get("source", "Unknown"),
        "execution_price_note": "Use this exchange feed as the analysis reference only; verify the live Binance Futures execution price and spread before any manual entry.",
        "signal": signal,
        "early_setup": {
            "direction": watch_signal, "status": watch_status,
            "momentum_scores": {k: round(v, 2) for k, v in momentum_scores.items()},
            "momentum_margin": round(momentum_margin, 2),
            "note": "Early setup is a watchlist hint only, not an entry signal. Confirm the main signal and manual risk levels before any trade."
        },
        "confidence_score": round(confidence, 2),
        "scores": {"LONG": round(long_score, 2), "SHORT": round(short_score, 2)},
        "timeframe_analysis": analyses,
        "liquidity_sweep": liquidity_sweep,
        "order_flow": {"cvd_base": cvd, "order_book_imbalance": imbalance,
                       "order_book_imbalance_method": derived.get("order_book_imbalance_method"),
                       "funding_rate_pct": round(funding_pct, 6), "funding_crowding": crowding,
                       "open_interest_change_30m_pct": round(oi_change_pct, 4) if oi_change_pct is not None else None,
                       "taker_buy_sell_ratio": taker_ratio},
        "ml_context": {"model_version": model.get("model_version"),
                       "data_source": model_source, "source_is_futures": model_source_is_futures,
                       "validation_passed": model_validation_passed,
                       "futures_source": model_is_futures, "market_alignment": model_market_alignment,
                       "signal": ml_bias},
        "risk_levels": risk,
        "data_quality": quality,
        "news_guard": news,
        "volatility_guard": {"blocked": volatility_block, "atr_5m_pct": round(atr_pct, 4), "atr_threshold_pct": 0.45, "range_spike": range_spike},
        "institutional_quality_gate": quality_gate,
        "warnings": warnings,
        "execution": "MANUAL_ONLY_NO_ORDERS"
    }


if __name__ == "__main__":
    result = run()
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(json.dumps({"signal": result["signal"], "confidence": result["confidence_score"],
                      "scores": result["scores"], "warnings": result["warnings"]}, indent=2))
