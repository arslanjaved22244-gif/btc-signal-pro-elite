#!/usr/bin/env python3
"""Optional Telegram manual-signal alerts. Credentials are GitHub Actions secrets only."""
import json, os, urllib.parse, urllib.request
def load(p):
    try:
        with open(p,encoding="utf-8") as f:return json.load(f)
    except (OSError,ValueError):return {}
def main():
    token=os.getenv("TELEGRAM_BOT_TOKEN"); chat=os.getenv("TELEGRAM_CHAT_ID")
    sig=load("signal_result.json"); adv=load("advanced_metrics.json")
    state=load("alert_state.json")
    side=sig.get("signal","WAIT")
    early=sig.get("early_setup",{})
    early_side=early.get("direction","WAIT")
    result={"status":"NOT_CONFIGURED","sent":False}
    if token and chat and side in ("LONG","SHORT") and state.get("last_signal")!=side:
        r=sig.get("risk_levels",{}); flow=sig.get("order_flow",{})
        msg=("BTC/USDT FUTURES SIGNAL: "+side+
             "\nConfidence score: "+str(sig.get("confidence_score"))+"%"+
             "\nEntry ref: "+str(r.get("entry_reference"))+
             "\nStop: "+str(r.get("stop_loss"))+
             "\nTP1: "+str(r.get("take_profit_1"))+" | TP2: "+str(r.get("take_profit_2"))+
             "\nFunding crowding: "+str(flow.get("funding_crowding"))+
             "\nReason: multi-timeframe trend + Futures order-flow confluence"+
             "\nManual trading only. No orders placed. Verify market/news before acting.")
        url="https://api.telegram.org/bot"+token+"/sendMessage"
        data=urllib.parse.urlencode({"chat_id":chat,"text":msg}).encode()
        req=urllib.request.Request(url,data=data)
        with urllib.request.urlopen(req,timeout=20) as res: payload=json.loads(res.read().decode())
        result={"status":"SENT" if payload.get("ok") else "FAILED","sent":bool(payload.get("ok"))}
        if result["sent"]: state["last_signal"]=side
    elif token and chat and early_side in ("LONG","SHORT") and state.get("last_early_setup")!=early_side:
        scores=early.get("momentum_scores",{})
        msg=("BTC/USDT EARLY SETUP WATCH: "+early_side+
             "\\nStatus: WATCH ONLY — NOT AN ENTRY SIGNAL"+
             "\\nMomentum scores: LONG "+str(scores.get("LONG"))+" | SHORT "+str(scores.get("SHORT"))+
             "\\nEntry reference: "+str(sig.get("risk_levels",{}).get("entry_reference"))+
             "\\nWait for the main confirmed signal and risk levels. No orders are placed.")
        url="https://api.telegram.org/bot"+token+"/sendMessage"
        data=urllib.parse.urlencode({"chat_id":chat,"text":msg}).encode()
        req=urllib.request.Request(url,data=data)
        with urllib.request.urlopen(req,timeout=20) as res: payload=json.loads(res.read().decode())
        result={"status":"WATCH_SENT" if payload.get("ok") else "FAILED","sent":bool(payload.get("ok"))}
        if result["sent"]: state["last_early_setup"]=early_side
    elif token and chat: result={"status":"NO_NEW_LONG_SHORT_SIGNAL","sent":False}
    with open("alert_state.json","w",encoding="utf-8") as f:json.dump(state,f,indent=2)
    with open("alert_status.json","w",encoding="utf-8") as f:json.dump(result,f,indent=2)
    print(json.dumps(result))
if __name__=="__main__":main()
