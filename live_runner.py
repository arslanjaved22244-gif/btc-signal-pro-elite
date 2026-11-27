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
events = []
lock = threading.Lock()
status = {"connected": False, "last_message": None, "last_error": None}

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

def monitor_worker():
    while True:
        started = time.time()
        try:
            result = subprocess.run([sys.executable, str(ROOT / "bot" / "monitor.py")],
                                    cwd=ROOT, timeout=55, capture_output=True, text=True)
            if result.stdout:
                print(result.stdout[-4000:], flush=True)
            if result.returncode:
                print("Monitor error:", (result.stderr or "unknown error")[-1200:], flush=True)
        except subprocess.TimeoutExpired:
            print("Monitor timed out; next cycle will retry", flush=True)
        except Exception as exc:
            print("Monitor runner error:", str(exc), flush=True)
        elapsed = time.time() - started
        time.sleep(max(5, 60 - elapsed))

if __name__ == "__main__":
    threading.Thread(target=liquidation_worker, daemon=True).start()
    threading.Thread(target=dashboard_server, daemon=True).start()
    monitor_worker()
