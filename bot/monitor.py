#!/usr/bin/env python3
"""BTC Signal Pro Elite public derivatives monitor. Signal-only; no Telegram or order placement."""
import json, urllib.request, urllib.parse, xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
OUT="market_snapshot.json"; STATE="state.json"; BASE="https://api.bybit.com"; NEWS_PAUSE_MIN=30
def now(): return datetime.now(timezone.utc)
def get_json(url):
 req=urllib.request.Request(url,headers={"User-Agent":"BTC-Signal-Pro-Elite/5.0","Accept":"application/json"})
 with urllib.request.urlopen(req,timeout=20) as r:return json.loads(r.read().decode())
def load_state():
 try:
  with open(STATE,encoding="utf-8") as f:return json.load(f)
 except Exception:return {}
def save_json(path,obj):
 with open(path,"w",encoding="utf-8") as f:json.dump(obj,f,indent=2)
def api(path,params):return get_json(BASE+path+"?"+urllib.parse.urlencode(params))
def news_scan():
 terms=["bitcoin","crypto","trump","tariff","war","iran","israel","sanction","federal reserve","fed","fomc","hack","sec","exchange","etf","attack"]
 queries=["Bitcoin OR crypto Trump tariff war Iran Israel sanctions Fed FOMC hack SEC","Trump statement markets Bitcoin crypto"]
 hits=[]; current=now()
 for query in queries:
  url="https://news.google.com/rss/search?q="+urllib.parse.quote(query)+"&hl=en-US&gl=US&ceid=US:en"
  try:
   req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 BTC-Signal-Pro-Elite"})
   with urllib.request.urlopen(req,timeout=15) as r:root=ET.fromstring(r.read())
   for item in root.findall(".//item"):
    title=item.findtext("title") or ""; pub=item.findtext("pubDate") or ""
    try:dt=parsedate_to_datetime(pub).astimezone(timezone.utc)
    except Exception:continue
    age=(current-dt).total_seconds()/60
    if -2<=age<=60 and any(t in title.lower() for t in terms):hits.append({"title":title,"published":dt.isoformat(),"age_minutes":round(age,1)})
  except Exception as exc:print("News feed warning:",str(exc)[:160])
 unique={x["title"]:x for x in hits};return sorted(unique.values(),key=lambda x:x["age_minutes"])
def market_data_bybit():
 tick=api("/v5/market/tickers",{"category":"linear","symbol":"BTCUSDT"})["result"]["list"][0]
 price=float(tick["lastPrice"]);funding=float(tick.get("fundingRate") or 0)
 oi=api("/v5/market/open-interest",{"category":"linear","symbol":"BTCUSDT","intervalTime":"5min","limit":2})["result"]["list"]
 oi_change=None
 if len(oi)>=2:
  old=float(oi[-1]["openInterest"]);new=float(oi[0]["openInterest"]);oi_change=(new-old)/old*100 if old else None
 book=api("/v5/market/orderbook",{"category":"linear","symbol":"BTCUSDT","limit":50})["result"]
 bids=sum(float(p)*float(q) for p,q in book["b"][:20]);asks=sum(float(p)*float(q) for p,q in book["a"][:20])
 imbalance=(bids-asks)/(bids+asks) if bids+asks else 0
 trades=api("/v5/market/recent-trade",{"category":"linear","symbol":"BTCUSDT","limit":500})["result"]["list"]
 buy=sell=0.0;large=[]
 for t in trades:
  qty=float(t["size"])
  if t["side"]=="Buy":buy+=qty
  else:sell+=qty
  if qty>=10:large.append({"side":t["side"].upper(),"qty_btc":round(qty,3),"price":float(t["price"]),"time":int(t["time"])})
 cvd=(buy-sell)/(buy+sell) if buy+sell else 0
 # Use recent trade-side volume for taker ratio. Bybit kline turnover is quote
 # notional, not taker-buy volume, so mixing it with base volume is invalid.
 taker=buy/(buy+sell) if buy+sell else .5
 return {"price":price,"funding_rate":funding,"funding_percent":funding*100,"open_interest":float(oi[0]["openInterest"]) if oi else None,"oi_change_5m_pct":oi_change,"orderbook_imbalance":imbalance,"cvd_proxy":cvd,"taker_buy_ratio":taker,"large_trades":sorted(large,key=lambda x:x["qty_btc"],reverse=True)[:8],"data_source":"Bybit public linear perpetual market API","as_of":now().isoformat()}

def market_data_okx():
 base="https://www.okx.com"
 def okx(path,params):
  url=base+path+"?"+urllib.parse.urlencode(params)
  return get_json(url)["data"]
 inst="BTC-USDT-SWAP"
 tick=okx("/api/v5/market/ticker",{"instId":inst})[0]
 funding_row=okx("/api/v5/public/funding-rate",{"instId":inst})[0]
 oi_rows=okx("/api/v5/public/open-interest",{"instType":"SWAP","instId":inst})
 book=okx("/api/v5/market/books",{"instId":inst,"sz":50})[0]
 bids=sum(float(x[0])*float(x[1]) for x in book["bids"][:20])
 asks=sum(float(x[0])*float(x[1]) for x in book["asks"][:20])
 imbalance=(bids-asks)/(bids+asks) if bids+asks else 0
 trades=okx("/api/v5/market/trades",{"instId":inst,"limit":500})
 buy=sell=0.0;large=[]
 for t in trades:
  qty=float(t["sz"])*0.01
  if t["side"]=="buy":buy+=qty
  else:sell+=qty
  if qty>=10:large.append({"side":t["side"].upper(),"qty_btc":round(qty,3),"price":float(t["px"]),"time":int(t["ts"])})
 cvd=(buy-sell)/(buy+sell) if buy+sell else 0
 taker=buy/(buy+sell) if buy+sell else .5
 oi=float(oi_rows[0]["oi"]) if oi_rows else None
 oi_change=None;oi_history=[]
 try:
  hist=okx("/api/v5/rubik/stat/contracts/open-interest-history",{"instId":inst,"period":"5m","limit":2})
  if len(hist)>=2:
   # OKX returns newest first; use oiUsd for a comparable USD notional change.
   newest=float(hist[0].get("oiUsd") or hist[0].get("oiCcy") or hist[0].get("oi"))
   older=float(hist[1].get("oiUsd") or hist[1].get("oiCcy") or hist[1].get("oi"))
   oi_change=(newest-older)/older*100 if older else None
   oi_history=[{"timestamp":x.get("ts"),"oi_usd":x.get("oiUsd"),"oi_coin":x.get("oiCcy"),"oi_contracts":x.get("oi")} for x in hist]
 except Exception as exc:
  print("OKX OI history warning:",str(exc)[:160])
 return {"price":float(tick["last"]),"funding_rate":float(funding_row["fundingRate"]),
  "funding_percent":float(funding_row["fundingRate"])*100,"open_interest":oi,"oi_change_5m_pct":oi_change,
  "oi_history":oi_history,
  "orderbook_imbalance":imbalance,"cvd_proxy":cvd,"taker_buy_ratio":taker,
  "large_trades":sorted(large,key=lambda x:x["qty_btc"],reverse=True)[:8],
  "data_source":"OKX public BTC-USDT-SWAP perpetual API","as_of":now().isoformat(),
  "oi_note":"OKX public 5-minute open-interest history when available"}

def market_data():
 errors=[]
 for name,fn in (("Bybit",market_data_bybit),("OKX",market_data_okx)):
  try:return fn()
  except Exception as exc:
   errors.append(name+": "+str(exc)[:180])
 raise RuntimeError("All derivatives feeds unavailable: "+" | ".join(errors))

def score_signal(m,paused):
 funding=m["funding_rate"];oi=m["oi_change_5m_pct"];book=m["orderbook_imbalance"];cvd=m["cvd_proxy"];taker=m["taker_buy_ratio"]
 long_crowded=funding>0.0005 and oi is not None and oi>0.5;short_crowded=funding< -0.0005 and oi is not None and oi>0.5
 if paused:return "WAIT","News safety pause active (minimum 30 minutes)"
 if cvd>0.08 and taker>0.53 and book>0.03 and not long_crowded:return "LONG","Positive taker flow, CVD and order-book imbalance"
 if cvd< -0.08 and taker<0.47 and book< -0.03 and not short_crowded:return "SHORT","Negative taker flow, CVD and order-book imbalance"
 return "WAIT","Derivatives flow is mixed or squeeze/crowding filters are active"
def main():
 state=load_state();m=market_data();news=news_scan();current=now();pause_until=state.get("pause_until")
 if news:
  try:old=datetime.fromisoformat(pause_until) if pause_until else current
  except Exception:old=current
  pause_until=max(old,current+timedelta(minutes=NEWS_PAUSE_MIN)).isoformat()
  state["pause_until"]=pause_until;state["pause_reason"]=news[0]["title"]
 try:paused=bool(pause_until and current<datetime.fromisoformat(pause_until))
 except Exception:paused=False
 signal,reason=score_signal(m,paused)
 m.update({"signal":signal,"reason":reason,"news_pause_active":paused,"pause_until":pause_until,"news_alerts":news[:8],"updated_at":current.isoformat(),"safety_note":"Signal-only; manual trading only. Large trades are trade-size alerts, not verified whale identities."})
 save_json(OUT,m);save_json(STATE,{"pause_until":pause_until,"pause_reason":state.get("pause_reason"),"last_check":current.isoformat(),"last_signal":signal})
 print(json.dumps(m,indent=2))
if __name__=="__main__":main()
