#!/usr/bin/env python3
"""Signal-only paper trader. Uses closed Futures candles; never sends orders."""
import json
from datetime import datetime, timezone

MARKET = "market_data.json"
SIGNAL = "signal_result.json"
OUT = "paper_trading.json"
ROUND_TRIP_COST_PCT = 0.14
HORIZON_BARS = 3  # 3 x 5m = 15 minutes


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def run():
    market = load(MARKET, {})
    signal = load(SIGNAL, {})
    tf = market.get("timeframes", {}).get("5m", {})
    candles = tf.get("candles", [])
    source = market.get("exchange", "unknown")
    quality = market.get("data_quality", {})
    if not quality.get("futures_only") or not quality.get("core_timeframes_ok") or not candles:
        state = load(OUT, {})
        state.update({"updated_at": datetime.now(timezone.utc).isoformat(),
                      "status": "PAUSED_BAD_OR_MISSING_FUTURES_DATA",
                      "warning": "Paper tracker only accepts verified Futures candles."})
        with open(OUT, "w", encoding="utf-8") as f: json.dump(state, f, indent=2)
        return state

    records = load(OUT, {"records": []}).get("records", [])
    # Use the last completed candle; ignore the currently forming candle.
    closed = candles[:-1] if len(candles) > 1 else []
    if not closed:
        return {"status": "WAITING_FOR_CLOSED_CANDLES", "records": records}
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
            "exchange": source,
            "side": side,
            "entry": price,
            "status": "PENDING" if side in ("LONG", "SHORT") else "ABSTAINED",
            "exit": None, "gross_return_pct": None, "net_return_pct": None
        })

    # Resolve each entry from the same exchange's 5m candle stream.
    by_time = {int(c.get("time", 0)): c for c in closed}
    candle_times = sorted(by_time)
    for item in records:
        if item.get("status") != "PENDING" or item.get("exchange") != source:
            continue
        try:
            pos = candle_times.index(int(item["entry_candle_time"]))
        except (ValueError, KeyError):
            continue
        exit_pos = pos + HORIZON_BARS
        if exit_pos >= len(candle_times):
            continue
        exit_candle = by_time[candle_times[exit_pos]]
        exit_price = float(exit_candle["close"])
        direction = 1 if item["side"] == "LONG" else -1
        gross = (exit_price - float(item["entry"])) / float(item["entry"]) * 100 * direction
        net = gross - ROUND_TRIP_COST_PCT
        item.update({"exit": exit_price, "exit_candle_time": candle_times[exit_pos],
                     "gross_return_pct": round(gross, 5), "net_return_pct": round(net, 5),
                     "status": "WIN" if net > 0 else "LOSS" if net < 0 else "BREAKEVEN"})
    records = records[-2000:]
    done = [x for x in records if x.get("status") in ("WIN", "LOSS", "BREAKEVEN")]
    pending = sum(x.get("status") == "PENDING" for x in records)
    abstained = sum(x.get("status") == "ABSTAINED" for x in records)
    wins = sum(x.get("status") == "WIN" for x in done)
    losses = sum(x.get("status") == "LOSS" for x in done)
    equity = peak = 1.0
    max_dd = 0.0
    for item in sorted(done, key=lambda x: x.get("exit_candle_time", 0)):
        equity *= 1 + float(item.get("net_return_pct", 0)) / 100
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak else 0)
    result = {
        "status": "ACTIVE", "mode": "PAPER_ONLY_NO_ORDERS",
        "source": source + " public Futures 5m candles",
        "horizon_minutes": 15, "estimated_round_trip_cost_pct": ROUND_TRIP_COST_PCT,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "current_signal": side, "current_signal_reason": signal.get("reason"),
        "closed_trades": len(done), "pending_trades": pending, "wait_abstentions": abstained,
        "wins": wins, "losses": losses,
        "win_rate_pct": round(wins / len(done) * 100, 2) if done else None,
        "net_return_pct_compounded": round((equity - 1) * 100, 4),
        "max_drawdown_pct": round(max_dd * 100, 4),
        "limitations": "15m fixed-close simulation only. Estimated 0.14% round-trip costs; no stop/target, funding, liquidation, intrabar fills or live order execution. Historical results are not predictive.",
        "records": records
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(json.dumps({k: result[k] for k in ("status", "closed_trades", "pending_trades", "wait_abstentions", "wins", "losses", "net_return_pct_compounded", "max_drawdown_pct")}, indent=2))
    return result


if __name__ == "__main__":
    run()
