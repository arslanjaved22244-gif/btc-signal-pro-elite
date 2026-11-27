#!/usr/bin/env python3
"""Signal-only paper trader plus non-actionable shadow forecast evaluation."""
import json
from datetime import datetime, timezone

MARKET = "market_data.json"
SIGNAL = "signal_result.json"
AI = "ai_result.json"
OUT = "paper_trading.json"
ROUND_TRIP_COST_PCT = 0.14
HORIZON_BARS = 3  # 3 x 5m = 15 minutes
TRADE_STATUSES = ("WIN", "LOSS", "BREAKEVEN")


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def resolve_records(records, candles, source, key, direction_key):
    by_time = {int(c.get("time", 0)): c for c in candles}
    times = sorted(by_time)
    for item in records:
        if item.get("status") != "PENDING":
            continue
        if item.get("exchange") != source:
            item["status"] = "UNRESOLVED_SOURCE_SWITCH"
            item["resolution_note"] = "Venue changed before the 15m outcome; excluded from results."
            continue
        try:
            pos = times.index(int(item["entry_candle_time"]))
        except (ValueError, KeyError):
            continue
        exit_pos = pos + HORIZON_BARS
        if exit_pos >= len(times):
            continue
        exit_price = float(by_time[times[exit_pos]]["close"])
        entry = float(item["entry"])
        direction = 1 if item[direction_key] == "LONG" else -1
        gross = (exit_price - entry) / entry * 100 * direction
        net = gross - ROUND_TRIP_COST_PCT
        item.update({
            "exit": exit_price, "exit_candle_time": times[exit_pos],
            "gross_return_pct": round(gross, 5), "net_return_pct": round(net, 5),
            "status": "WIN" if net > 0 else "LOSS" if net < 0 else "BREAKEVEN"
        })


def performance(records):
    done = [x for x in records if x.get("status") in TRADE_STATUSES]
    wins = sum(x.get("status") == "WIN" for x in done)
    losses = sum(x.get("status") == "LOSS" for x in done)
    equity = peak = 1.0
    max_dd = 0.0
    for item in sorted(done, key=lambda x: x.get("exit_candle_time", 0)):
        equity *= 1 + float(item.get("net_return_pct", 0)) / 100
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak else 0)
    returns = [float(x.get("net_return_pct", 0)) for x in done]
    directional_hits = sum(float(x.get("gross_return_pct", 0)) > 0 for x in done)
    return {
        "closed": len(done), "wins": wins, "losses": losses,
        "win_rate_pct": round(wins / len(done) * 100, 2) if done else None,
        "gross_directional_accuracy_pct": round(directional_hits / len(done) * 100, 2) if done else None,
        "net_return_pct_compounded": round((equity - 1) * 100, 4),
        "average_net_return_pct": round(sum(returns) / len(returns), 5) if returns else None,
        "max_drawdown_pct": round(max_dd * 100, 4)
    }


def run():
    market = load(MARKET, {})
    signal = load(SIGNAL, {})
    ai = load(AI, {})
    tf = market.get("timeframes", {}).get("5m", {})
    candles = tf.get("candles", [])
    source = market.get("exchange", "unknown")
    quality = market.get("data_quality", {})
    state = load(OUT, {})
    records = state.get("records", [])
    shadow = state.get("shadow_forecasts", [])

    if not quality.get("futures_only") or not quality.get("core_timeframes_ok") or not candles:
        state.update({
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "status": "PAUSED_BAD_OR_MISSING_FUTURES_DATA",
            "warning": "Paper tracker only accepts verified Futures candles."
        })
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        return state

    # The market publisher supplies completed candles only.
    closed = candles
    last = closed[-1]
    stamp = int(last.get("time", 0))
    price = float(last["close"])
    side = signal.get("signal", "WAIT")
    if side not in ("LONG", "SHORT", "WAIT"):
        side = "WAIT"

    if not any(x.get("entry_candle_time") == stamp for x in records):
        records.append({
            "entry_candle_time": stamp,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "exchange": source, "side": side, "entry": price,
            "status": "PENDING" if side in ("LONG", "SHORT") else "ABSTAINED",
            "exit": None, "gross_return_pct": None, "net_return_pct": None
        })

    # Shadow evaluation records the raw ML direction even when the safety gate
    # blocks live signals. These records are research-only and never authorize trades.
    ai_source = str(ai.get("data_source", ""))
    ai_tf_source = str(ai.get("data_source_by_timeframe", {}).get("5m", ""))
    ai_generated_at = str(ai.get("generated_at", ""))
    ai_is_fresh = False
    try:
        parsed_ai_time = datetime.fromisoformat(ai_generated_at.replace("Z", "+00:00"))
        if parsed_ai_time.tzinfo is None:
            parsed_ai_time = parsed_ai_time.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - parsed_ai_time.astimezone(timezone.utc)).total_seconds()
        ai_is_fresh = -60 <= age_seconds <= 600
    except (TypeError, ValueError):
        pass
    same_venue = (source.lower() in ai_source.lower()) or (source.lower() in ai_tf_source.lower())
    model_scores = ai.get("class_scores", {})
    directional_scores = {
        "LONG": float(model_scores.get("LONG", 0) or 0),
        "SHORT": float(model_scores.get("SHORT", 0) or 0)
    }
    raw_side = max(directional_scores, key=directional_scores.get) if directional_scores else "WAIT"
    wait_score = float(model_scores.get("WAIT", 0) or 0)
    if not same_venue or not ai_source or "Futures" not in ai_source or max(directional_scores.values(), default=0) <= wait_score:
        raw_side = "WAIT"
    # Do not score the same model output repeatedly on successive market snapshots.
    # Only record a fresh, timestamped model generation once.
    already_recorded_model = bool(ai_generated_at) and any(
        x.get("model_generated_at") == ai_generated_at for x in shadow
    )
    if ai_is_fresh and not already_recorded_model and not any(x.get("entry_candle_time") == stamp for x in shadow):
        shadow.append({
            "entry_candle_time": stamp,
            "model_generated_at": ai_generated_at,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "exchange": source, "side": raw_side, "entry": price,
            "status": "PENDING" if raw_side in ("LONG", "SHORT") else "ABSTAINED",
            "actionable": False,
            "validation_gate_passed": ai.get("validation_gate", {}).get("passed") is True,
            "model_version": ai.get("model_version"),
            "note": "Shadow-only raw ML forecast. Not a trading signal; validation gate may have failed."
        })

    resolve_records(records, closed, source, "records", "side")
    resolve_records(shadow, closed, source, "shadow_forecasts", "side")
    records = records[-2000:]
    shadow = shadow[-2000:]

    stats = performance(records)
    shadow_stats = performance(shadow)
    pending = sum(x.get("status") == "PENDING" for x in records)
    abstained = sum(x.get("status") == "ABSTAINED" for x in records)
    shadow_pending = sum(x.get("status") == "PENDING" for x in shadow)
    shadow_abstained = sum(x.get("status") == "ABSTAINED" for x in shadow)
    result = {
        "status": "ACTIVE", "mode": "PAPER_ONLY_NO_ORDERS",
        "source": source + " public Futures 5m candles",
        "horizon_minutes": 15, "estimated_round_trip_cost_pct": ROUND_TRIP_COST_PCT,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "current_signal": side, "current_signal_reason": signal.get("reason"),
        "closed_trades": stats["closed"], "pending_trades": pending,
        "wait_abstentions": abstained, "wins": stats["wins"], "losses": stats["losses"],
        "win_rate_pct": stats["win_rate_pct"],
        "net_return_pct_compounded": stats["net_return_pct_compounded"],
        "max_drawdown_pct": stats["max_drawdown_pct"],
        "shadow_evaluation": {
            "mode": "NON_ACTIONABLE_RAW_MODEL_FORECASTS",
            "closed_forecasts": shadow_stats["closed"],
            "pending_forecasts": shadow_pending,
            "abstentions": shadow_abstained,
            "wins": shadow_stats["wins"], "losses": shadow_stats["losses"],
            "directional_accuracy_pct": shadow_stats["gross_directional_accuracy_pct"],
            "net_win_rate_pct": shadow_stats["win_rate_pct"],
            "average_net_return_pct": shadow_stats["average_net_return_pct"],
            "net_return_pct_compounded": shadow_stats["net_return_pct_compounded"],
            "validation_gate_passed_now": ai.get("validation_gate", {}).get("passed") is True,
            "latest_model_timestamp": ai_generated_at or None,
            "latest_model_fresh": ai_is_fresh,
            "note": "Research-only forecast tracking. Each fresh model generation is counted once; stale or repeated AI snapshots are skipped. Forecasts never authorize trades."
        },
        "limitations": "15m fixed-close simulation only. Estimated 0.14% round-trip costs; no stop/target, funding, liquidation, intrabar fills or live order execution. Shadow forecasts are non-actionable. Historical results are not predictive.",
        "records": records,
        "shadow_forecasts": shadow
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(json.dumps({
        "status": result["status"], "closed_trades": result["closed_trades"],
        "wait_abstentions": result["wait_abstentions"],
        "shadow_closed": shadow_stats["closed"], "shadow_pending": shadow_pending,
        "shadow_abstentions": shadow_abstained
    }, indent=2))
    return result


if __name__ == "__main__":
    run()
