#!/usr/bin/env python3
"""24/7 signal-only runner: periodic derivatives/news monitor + persistent liquidation WS + dashboard."""
import json, os, subprocess, sys, threading, time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
from pathlib import Path
import websocket

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "liquidation_data.json"
URL = "wss://fstream.binance.com/ws/btcusdt@forceOrder"
lock = threading.Lock()
status = {"connected": False, "last_message": None, "last_error": None}
try:
    existing = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    events = [item for item in existing.get("events", [])
              if isinstance(item, dict) and item.get("time") is not None]
    events = events[-500:]
except (OSError, ValueError, TypeError):
    events = []

def utc():
    return datetime.now(timezone.utc).isoformat()

def save_liquidations():
    with lock:
        snapshot = list(events[-500:])
        connected = status["connected"]
        last_message = status["last_message"]
        last_error = status["last_error"]
    long_usd = sum(x["notional_usdt"] for x in snapshot if x["side"] == "SELL")
    short_usd = sum(x["notional_usdt"] for x in snapshot if x["side"] == "BUY")
    data = {
        "available": connected, "connected": connected, "continuous": bool(connected),
        "status": "CONNECTED" if connected else "DISCONNECTED",
        "source": "Binance USD-M forceOrder WebSocket",
        "generated_at": utc(), "last_message_at": last_message,
        "event_count": len(snapshot),
        "retained_session_events": len(snapshot),
        "last_event_time": snapshot[-1].get("time") if snapshot else None,
        "long_liquidation_notional_usdt": round(long_usd, 2),
        "short_liquidation_notional_usdt": round(short_usd, 2),
        "events": snapshot[-100:], "last_error": last_error,
        "warning": "Binance forceOrder is a public stream of sampled liquidation orders, not a complete market-wide liquidation tape."
    }
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(OUT)

def on_open(ws):
    with lock:
        status["connected"] = True
        status["last_error"] = None
    save_liquidations()
    print("Liquidation WebSocket connected", flush=True)

def on_message(ws, message):
    try:
        order = json.loads(message).get("o", {})
        price = float(order.get("ap", 0) or 0)
        qty = float(order.get("q", 0) or 0)
        item = {"time": order.get("T"), "side": order.get("S"),
                "price": price, "quantity": qty, "notional_usdt": price * qty}
        with lock:
            events.append(item)
            del events[:-500]
            status["last_message"] = utc()
        save_liquidations()
    except Exception as exc:
        with lock:
            status["last_error"] = str(exc)[:180]

def on_error(ws, error):
    with lock:
        status["last_error"] = str(error)[:180]
    print("Liquidation WebSocket warning:", str(error)[:180], flush=True)

def on_close(ws, code, message):
    with lock:
        status["connected"] = False
    save_liquidations()
    print("Liquidation WebSocket disconnected; reconnecting", flush=True)

def liquidation_worker():
    while True:
        try:
            ws = websocket.WebSocketApp(URL, on_open=on_open, on_message=on_message,
                                        on_error=on_error, on_close=on_close)
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as exc:
            on_error(None, exc)
        with lock:
            status["connected"] = False
        save_liquidations()
        time.sleep(5)

def dashboard_server():
    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/healthz":
                with lock:
                    connected = status["connected"]
                    last_message = status["last_message"]
                    last_error = status["last_error"]
                body = json.dumps({
                    "service": "BTC Signal Pro Elite live runner",
                    "process_alive": True,
                    "liquidation_websocket_connected": connected,
                    "continuous": connected,
                    "last_message_at": last_message,
                    "last_error": last_error,
                    "checked_at": utc()
                }).encode("utf-8")
                self.send_response(200 if connected else 503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            return super().do_GET()

        def log_message(self, fmt, *args):
            print("HTTP:", fmt % args, flush=True)

    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    os.chdir(ROOT)
    print("Dashboard and /healthz listening on", port, flush=True)
    server.serve_forever()

def run_step(name, script, timeout=100):
    result = subprocess.run([sys.executable, str(ROOT / script)], cwd=ROOT,
                            timeout=timeout, capture_output=True, text=True)
    if result.stdout:
        print(name + " output:", result.stdout[-2500:], flush=True)
    if result.returncode:
        print(name + " failed:", (result.stderr or "unknown error")[-1500:], flush=True)
        return False
    print(name + " completed", flush=True)
    return True


def monitor_worker():
    while True:
        started = time.time()
        # Refresh the actual market and signal snapshots in this persistent process.
        # A failed market collection must never allow a stale snapshot to authorize a signal.
        market_ok = False
        try:
            market_ok = run_step("Futures market collection", "market_data.py", timeout=100)
            if market_ok:
                news_ok = run_step("News/event risk scan", "news_guard.py", timeout=60)
                signal_ok = news_ok and run_step("Signal confluence engine", "signal_engine.py", timeout=45)
                if signal_ok:
                    run_step("Paper-trade journal (no orders)", "paper_trader.py", timeout=45)
                    run_step("Advanced metrics", "advanced_modules.py", timeout=60)
            else:
                print("Signal update skipped: latest market collection failed; existing signal must be treated as stale.", flush=True)
            run_step("Derivatives and news monitor", "bot/monitor.py", timeout=55)
        except subprocess.TimeoutExpired as exc:
            print("Live scan timed out:", str(exc), flush=True)
        except Exception as exc:
            print("Live monitor runner error:", str(exc), flush=True)
        elapsed = time.time() - started
        time.sleep(max(5, 60 - elapsed))

if __name__ == "__main__":
    threading.Thread(target=liquidation_worker, daemon=True).start()
    threading.Thread(target=dashboard_server, daemon=True).start()
    monitor_worker()
