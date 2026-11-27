#!/usr/bin/env python3
"""Short sampled Binance force-order stream window; not a continuous liquidation tape."""
import json, time
from datetime import datetime, timezone
import websocket

URL="wss://fstream.binance.com/ws/btcusdt@forceOrder"
WINDOW=int(__import__("os").getenv("LIQUIDATION_SAMPLE_SECONDS","10"))
events=[]
errors=[]
connected=False
start=time.time()
def on_open(ws):
    global connected
    connected=True

def on_message(ws, message):
    try:
        x=json.loads(message).get("o",{})
        events.append({"time":x.get("T"),"side":x.get("S"),"price":float(x.get("ap",0) or 0),
                       "quantity":float(x.get("q",0) or 0),
                       "notional_usdt":float(x.get("ap",0) or 0)*float(x.get("q",0) or 0)})
    except Exception: pass
def on_error(ws, error):
    errors.append(str(error)[:180])
    print("Liquidation websocket warning:",str(error)[:180])
ws=websocket.WebSocketApp(URL,on_open=on_open,on_message=on_message,on_error=on_error)
def run():
    import threading
    t=threading.Thread(target=lambda:ws.run_forever(ping_interval=0),daemon=True); t.start()
    deadline=time.time()+WINDOW
    while time.time()<deadline and t.is_alive(): time.sleep(.5)
    try: ws.close()
    except Exception: pass
    t.join(timeout=3)
    longs=sum(x["notional_usdt"] for x in events if x["side"]=="SELL")
    shorts=sum(x["notional_usdt"] for x in events if x["side"]=="BUY")
    out={"available":connected,"source":"Binance USD-M forceOrder WebSocket",
         "generated_at":datetime.now(timezone.utc).isoformat(),"sample_window_seconds":WINDOW,
         "continuous":False,"connected":connected,"errors":errors,"event_count":len(events),"long_liquidation_notional_usdt":round(longs,2),
         "short_liquidation_notional_usdt":round(shorts,2),"events":events[-100:],
         "warning":"Only events observed during this short GitHub Actions sample are included; this is not continuous market-wide liquidation coverage." if connected else "WebSocket did not connect; liquidation data is unavailable."}
    with open("liquidation_data.json","w") as f: json.dump(out,f,indent=2)
    print(json.dumps({k:out[k] for k in ("event_count","sample_window_seconds","long_liquidation_notional_usdt","short_liquidation_notional_usdt")},indent=2))
if __name__=="__main__": run()
