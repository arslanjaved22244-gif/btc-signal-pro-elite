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
 # Google News covers global headlines; Trump's Truth is an independent archive RSS
 # of public @realDonaldTrump Truth Social posts (not an official platform API).
 terms=["bitcoin","crypto","trump","tariff","war","iran","israel","sanction","federal reserve","fed","fomc","hack","sec","exchange","etf","attack","oil","energy","inflation","interest rate","china","russia","ukraine","nato","military","treasury","dollar","trade deal","export","ban","embargo"]
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
    if -2<=age<=90 and any(t in title.lower() for t in terms):
     hits.append({"title":title,"published":dt.isoformat(),"age_minutes":round(age,1),"source":"Google News RSS","url":item.findtext("link") or ""})
  except Exception as exc:print("Google News warning:",str(exc)[:160])
 # Third-party archive says it checks @realDonaldTrump posts every few minutes.
 try:
  req=urllib.request.Request("https://www.trumpstruth.org/feed",headers={"User-Agent":"Mozilla/5.0 BTC-Signal-Pro-Elite","Accept":"application/rss+xml, application/xml, text/xml"})
  with urllib.request.urlopen(req,timeout=15) as r:root=ET.fromstring(r.read())
  for item in root.findall(".//item"):
   title=item.findtext("title") or "Donald Trump Truth Social post"
   desc=item.findtext("description") or ""
   pub=item.findtext("pubDate") or ""
   try:dt=parsedate_to_datetime(pub).astimezone(timezone.utc)
   except Exception:continue
   age=(current-dt).total_seconds()/60
   body=(title+" "+desc).lower()
   # Only posts plausibly relevant to market/geopolitical risk trigger a pause.
   risk_terms=["bitcoin","crypto","cryptocurrency","tariff","trade","china","russia","ukraine","iran","israel","war","military","oil","energy","sanction","federal reserve","fed","interest rate","inflation","dollar","treasury","sec","bank","tax","export","embargo","attack","nuclear","venezuela"]
   if -2<=age<=90 and any(t in body for t in risk_terms):
    hits.append({"title":title[:280],"published":dt.isoformat(),"age_minutes":round(age,1),"source":"Trump's Truth archive RSS (third-party)","url":item.findtext("link") or ""})
 except Exception as exc:print("Trump Truth archive warning:",str(exc)[:160])
 unique={(x["source"],x["title"]):x for x in hits}
 return sorted(unique.values(),key=lambda x:x["age_minutes"])

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
   # OKX Rubik endpoint returns rows [ts, oi contracts, oi coin, oi USD], newest first.
   newest=float(hist[0][3] or hist[0][2] or hist[0][1])
   older=float(hist[1][3] or hist[1][2] or hist[1][1])
   oi_change=(newest-older)/older*100 if older else None
   oi_history=[{"timestamp":x[0],"oi_contracts":x[1],"oi_coin":x[2],"oi_usd":x[3]} for x in hist]
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

def update_prediction_performance():
 """Log AI forecasts and score directional outcomes against Binance USD-M prices."""
 path="prediction_performance.json"
 try:
  with open(path,encoding="utf-8") as f: data=json.load(f)
 except Exception: data={"source":"OKX BTC-USDT-SWAP public ticker","records":[],"updated_at":None}
 records=data.get("records",[])
 try:
  ai=get_json("https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT-SWAP")
  price=float(ai["data"][0]["last"])
  ai_result=get_json("https://raw.githubusercontent.com/arslanjaved22244-gif/btc-signal-pro-elite/main/ai_result.json") if False else None
  req=urllib.request.Request("https://raw.githubusercontent.com/arslanjaved22244-gif/btc-signal-pro-elite/main/ai_result.json",headers={"User-Agent":"BTC-Signal-Pro-Elite/5.0"})
  with urllib.request.urlopen(req,timeout=15) as r: model=json.loads(r.read().decode())
 except Exception as exc:
  data["last_error"]=str(exc)[:180]
  data["updated_at"]=now().isoformat()
  save_json(path,data)
  return
 generated=model.get("generated_at")
 if generated and not any(x.get("model_generated_at")==generated for x in records):
  forecasts=model.get("advanced_intelligence",{}).get("tree_ensemble",{}).get("forecasts",{})
  if not forecasts: forecasts=model.get("advanced_intelligence",{}).get("multi_horizon_forecasts",{})
  for horizon,mins in (("15m",15),("30m",30),("60m",60),("4h",240)):
   fc=forecasts.get(horizon,{})
   direction=fc.get("signal","WAIT")
   if direction not in ("LONG","SHORT","WAIT"): direction="WAIT"
   records.append({"model_generated_at":generated,"logged_at":now().isoformat(),"horizon":horizon,
    "horizon_minutes":mins,"direction":direction,"reference_price":price,"outcome":None,
    "evaluated_at":None,"realized_change_pct":None,"correct":None,
    "validation_status":fc.get("status","unknown")})
 # Score only after each forecast horizon has elapsed. Directional hit rate excludes WAIT calls.
 current=now()
 for item in records:
  if item.get("outcome") is not None: continue
  try: due=datetime.fromisoformat(item["logged_at"])+timedelta(minutes=int(item["horizon_minutes"]))
  except Exception: continue
  if current<due: continue
  try:
   end_data=get_json("https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT-SWAP")
   end_price=float(end_data["data"][0]["last"])
   start=float(item["reference_price"]); change=(end_price-start)/start*100 if start else 0
   item["realized_change_pct"]=round(change,5);item["evaluated_at"]=current.isoformat()
   if item["direction"]=="LONG": item["correct"]=change>0;item["outcome"]="UP" if change>0 else "DOWN_OR_FLAT"
   elif item["direction"]=="SHORT": item["correct"]=change<0;item["outcome"]="DOWN" if change<0 else "UP_OR_FLAT"
   else: item["correct"]=None;item["outcome"]="ABSTAINED"
  except Exception as exc:
   item["evaluation_error"]=str(exc)[:120]
 # Keep a bounded history; calculate hit rate only over evaluated directional calls.
 records=records[-500:]
 stats={}
 for horizon in ("15m","30m","60m","4h"):
  group=[x for x in records if x.get("horizon")==horizon and x.get("outcome") is not None]
  directional=[x for x in group if x.get("direction") in ("LONG","SHORT") and isinstance(x.get("correct"),bool)]
  wins=sum(1 for x in directional if x["correct"])
  stats[horizon]={"evaluated":len(group),"directional_calls":len(directional),"correct":wins,
   "directional_accuracy_pct":round(100*wins/len(directional),2) if directional else None,
   "wait_abstentions":sum(1 for x in group if x.get("direction")=="WAIT")}
 data={"source":"OKX BTC-USDT-SWAP public ticker; directional close comparison only",
  "updated_at":current.isoformat(),"model_generated_at":generated,"current_price":price,
  "stats":stats,"records":records,
  "limitations":"Not a trade P&L, win-rate guarantee, or stop/target simulation. LONG/SHORT accuracy is directional only; WAIT is counted as abstention."}
 save_json(path,data)

def score_signal(m,paused):
 funding=m["funding_rate"];oi=m["oi_change_5m_pct"];book=m["orderbook_imbalance"];cvd=m["cvd_proxy"];taker=m["taker_buy_ratio"]
 long_crowded=funding>0.0005 and oi is not None and oi>0.5;short_crowded=funding< -0.0005 and oi is not None and oi>0.5
 if paused:return "WAIT","News safety pause active (minimum 30 minutes)"
 if cvd>0.08 and taker>0.53 and book>0.03 and not long_crowded:return "LONG","Positive taker flow, CVD and order-book imbalance"
 if cvd< -0.08 and taker<0.47 and book< -0.03 and not short_crowded:return "SHORT","Negative taker flow, CVD and order-book imbalance"
 return "WAIT","Derivatives flow is mixed or squeeze/crowding filters are active"
def main():
 state=load_state();m=market_data();news=news_scan();update_prediction_performance();current=now();pause_until=state.get("pause_until")
 # Build a genuine rolling 5-minute OI delta from consecutive public OI snapshots.
 # The GitHub Actions cache preserves this lightweight sample between runs.
 oi_now=m.get("open_interest")
 if oi_now is not None:
  try:
   prev=state.get("oi_sample") or {}
   prev_value=float(prev.get("value"));prev_time=datetime.fromisoformat(prev.get("time"))
   age=(current-prev_time).total_seconds()
   if 240<=age<=720 and prev_value:
    m["oi_change_5m_pct"]=(float(oi_now)-prev_value)/prev_value*100
    m["oi_change_sample_age_seconds"]=round(age,1)
   else:
    m["oi_change_5m_pct"]=None
    m["oi_note"]="Waiting for a valid 4-12 minute OI sample pair"
  except Exception:
   m["oi_change_5m_pct"]=None
   m["oi_note"]="Collecting first OI baseline sample"
  state["oi_sample"]={"value":float(oi_now),"time":current.isoformat()}

 if news:
  try:old=datetime.fromisoformat(pause_until) if pause_until else current
  except Exception:old=current
  pause_until=max(old,current+timedelta(minutes=NEWS_PAUSE_MIN)).isoformat()
  state["pause_until"]=pause_until;state["pause_reason"]=news[0]["title"]
 try:paused=bool(pause_until and current<datetime.fromisoformat(pause_until))
 except Exception:paused=False
 signal,reason=score_signal(m,paused)
 m.update({"signal":signal,"reason":reason,"news_pause_active":paused,"pause_until":pause_until,"news_alerts":news[:8],"updated_at":current.isoformat(),"safety_note":"Signal-only; manual trading only. Large trades are trade-size alerts, not verified whale identities. Trump social posts are monitored through a third-party archive RSS, not an official API."})
 save_json(OUT,m);save_json(STATE,{"pause_until":pause_until,"pause_reason":state.get("pause_reason"),"last_check":current.isoformat(),"last_signal":signal,"oi_sample":state.get("oi_sample")})
 print(json.dumps(m,indent=2))
if __name__=="__main__":main()
