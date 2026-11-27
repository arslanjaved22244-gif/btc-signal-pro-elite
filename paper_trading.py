#!/usr/bin/env python3
"""Cost-aware, signal-only paper trading ledger for BTC perpetual Futures."""
import json
from pathlib import Path
from datetime import datetime, timezone

MARKET_FILE = Path("market_data.json")
SIGNAL_FILE = Path("signal_result.json")
MODEL_FILE = Path("ai_result.json")
OUTPUT_FILE = Path("paper_trading.json")
ROUND_TRIP_COST_PCT = 0.14
HOLD_CANDLES = 3
MAX_RECORDS = 2000


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def futures_model_approved(market, signal, model):
    quality = market.get("data_quality", {})
    source = str(model.get("data_source", ""))
    gate = model.get("validation_gate", {})
    news = signal.get("news_guard", {})
    return bool(
        quality.get("futures_only") is True
        and quality.get("core_timeframes_ok") is True
        and quality.get("five_minute_data_fresh") is True
        and gate.get("passed") is True
        and gate.get("futures_source_passed") is True
        and "Futures" in source and "Spot" not in source
        and news.get("block") is not True
        and news.get("available") is True
    )


def settle_pending(records, candles):
    by_time = sorted(candles, key=lambda x: int(x.get("time", 0)))
    for trade in records:
        if trade.get("status") != "PENDING":
            continue
        due = int(trade["due_time"])
        exit_candle = next((c for c in by_time if int(c.get("time", 0)) >= due), None)
        if not exit_candle:
            continue
        entry = float(trade["entry_price"])
        exit_price = float(exit_candle["close"])
        if entry <= 0:
            trade["status"] = "INVALID"
            continue
        raw = (exit_price - entry) / entry * 100.0
        gross = raw if trade["side"] == "LONG" else -raw
        net = gross - ROUND_TRIP_COST_PCT
        trade.update({
            "status": "CLOSED",
            "exit_time": int(exit_candle["time"]),
            "exit_price": exit_price,
            "gross_return_pct": round(gross, 5),
            "net_return_pct": round(net, 5),
            "result": "WIN" if net > 0 else "LOSS" if net < 0 else "BREAKEVEN",
            "cost_assumption_pct": ROUND_TRIP_COST_PCT,
        })


def summarize(records):
    closed = [x for x in records if x.get("status") == "CLOSED"]
    wins = sum(x.get("net_return_pct", 0) > 0 for x in closed)
    total_net = sum(float(x.get("net_return_pct", 0)) for x in closed)
    equity = 1.0
    for item in closed:
        equity *= 1.0 + float(item.get("net_return_pct", 0)) / 100.0
    return {
        "closed_trades": len(closed),
        "pending_trades": sum(x.get("status") == "PENDING" for x in records),
        "abstentions": sum(x.get("status") == "ABSTAINED" for x in records),
        "wins": wins,
        "losses": len(closed) - wins,
        "net_win_rate_pct": round(100.0 * wins / len(closed), 2) if closed else None,
        "sum_net_return_pct": round(total_net, 5),
        "compounded_net_return_pct": round((equity - 1.0) * 100.0, 5),
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
    }


def process_snapshot(state, market, signal, model):
    records = list(state.get("records", []))
    candles = market.get("timeframes", {}).get("5m", {}).get("candles", [])
    if not candles:
        state["records"] = records[-MAX_RECORDS:]
        state["summary"] = summarize(records)
        state["warning"] = "No 5m Futures candles available; no paper entry created."
        return state

    settle_pending(records, candles)
    last = max(candles, key=lambda x: int(x.get("time", 0)))
    candle_time = int(last["time"])
    if state.get("last_candle_time") != candle_time:
        side = signal.get("signal", "WAIT")
        approved = futures_model_approved(market, signal, model)
        if side not in ("LONG", "SHORT") or not approved:
            records.append({
                "candle_time": candle_time,
                "logged_at": datetime.now(timezone.utc).isoformat(),
                "status": "ABSTAINED",
                "side": "WAIT",
                "reason": "Signal WAIT or Futures/news/validation safety gate not fully passed.",
                "source": market.get("exchange", "unknown"),
            })
        else:
            entry = float(last["close"])
            records.append({
                "candle_time": candle_time,
                "logged_at": datetime.now(timezone.utc).isoformat(),
                "status": "PENDING",
                "side": side,
                "entry_time": candle_time,
                "due_time": candle_time + HOLD_CANDLES * 300000,
                "entry_price": entry,
                "source": market.get("exchange", "unknown"),
                "cost_assumption_pct": ROUND_TRIP_COST_PCT,
                "hold_minutes": 15,
            })
        state["last_candle_time"] = candle_time

    state["records"] = records[-MAX_RECORDS:]
    state["summary"] = summarize(state["records"])
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state["method"] = "One signal per completed 5m Futures candle; 15m close-to-close paper hold; round-trip fee/slippage allowance deducted."
    state["warning"] = "Simulation only. No orders are sent. Results are not live fills and do not guarantee future performance."
    return state


def main():
    state = load_json(OUTPUT_FILE)
    result = process_snapshot(
        state,
        load_json(MARKET_FILE),
        load_json(SIGNAL_FILE),
        load_json(MODEL_FILE),
    )
    OUTPUT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"summary": result.get("summary"), "updated_at": result.get("updated_at"),
                      "last_candle_time": result.get("last_candle_time")}, indent=2))


if __name__ == "__main__":
    main()
