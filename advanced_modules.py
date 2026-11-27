#!/usr/bin/env python3
"""Technical intelligence, chronological cost-aware backtest and 10x risk calculator."""
import json, math, os
from datetime import datetime, timezone

def load(p):
    try:
        with open(p, encoding="utf-8") as f: return json.load(f)
    except (OSError, ValueError): return {}

def ema(xs, n):
    if not xs: return []
    a=2/(n+1); out=[xs[0]]
    for x in xs[1:]: out.append(a*x+(1-a)*out[-1])
    return out

def rsi(xs, n=14):
    if len(xs)<n+1: return None
    ds=[xs[i]-xs[i-1] for i in range(1,len(xs))]
    g=sum(max(x,0) for x in ds[:n])/n; l=sum(max(-x,0) for x in ds[:n])/n
    for x in ds[n:]:
        g=(g*(n-1)+max(x,0))/n; l=(l*(n-1)+max(-x,0))/n
    return 100 if l==0 and g else 50 if l==0 else 100-100/(1+g/l)

def macd(xs):
    if len(xs)<35: return {}
    fast,slow=ema(xs,12),ema(xs,26); line=[a-b for a,b in zip(fast,slow)]
    sig=ema(line,9)
    return {"macd":round(line[-1],4),"signal":round(sig[-1],4),
            "histogram":round(line[-1]-sig[-1],4),
            "bullish":line[-1]>sig[-1],"cross_up":len(line)>1 and line[-2]<=sig[-2] and line[-1]>sig[-1],
            "cross_down":len(line)>1 and line[-2]>=sig[-2] and line[-1]<sig[-1]}

def structure(rows, lookback=3):
    highs=[x["high"] for x in rows]; lows=[x["low"] for x in rows]
    sh=[]; sl=[]
    for i in range(lookback,len(rows)-lookback):
        if highs[i]==max(highs[i-lookback:i+lookback+1]): sh.append((i,highs[i]))
        if lows[i]==min(lows[i-lookback:i+lookback+1]): sl.append((i,lows[i]))
    last=rows[-1]["close"]
    prev_h=sh[-1][1] if sh else None; prev_l=sl[-1][1] if sl else None
    prior_h=sh[-2][1] if len(sh)>1 else None; prior_l=sl[-2][1] if len(sl)>1 else None
    trend="RANGE"
    if prev_h is not None and prev_l is not None:
        if prior_h is not None and prior_l is not None and prev_h>prior_h and prev_l>prior_l: trend="UPTREND"
        elif prior_h is not None and prior_l is not None and prev_h<prior_h and prev_l<prior_l: trend="DOWNTREND"
    event="NONE"
    if prev_h is not None and last>prev_h: event="BULLISH_BOS"
    elif prev_l is not None and last<prev_l: event="BEARISH_BOS"
    return {"trend":trend,"event":event,"last_swing_high":prev_h,"last_swing_low":prev_l,
            "note":"Confirmed pivots use candles on both sides; most recent pivot is delayed by the confirmation window."}

def indicators(rows):
    if len(rows)<205: return {"available":False}
    c=[x["close"] for x in rows]; e50=ema(c,50)[-1]; e200=ema(c,200)[-1]; m=macd(c)
    tr=[]
    for p,x in zip(rows[:-1],rows[1:]):
        tr.append(max(x["high"]-x["low"],abs(x["high"]-p["close"]),abs(x["low"]-p["close"])))
    av=sum(tr[-14:])/14 if len(tr)>=14 else None
    return {"available":True,"ema50":round(e50,2),"ema200":round(e200,2),
            "rsi14":round(rsi(c),2) if rsi(c) is not None else None,
            "macd":m,"structure":structure(rows),
            "volatility_atr14":round(av,2) if av is not None else None,
            "atr_pct":round(av/c[-1]*100,4) if av and c[-1] else None}

def backtest(rows, fee=0.0005, slip=0.0002):
    # Deterministic MACD/EMA strategy, chronological 60/20/20 split.
    # Test segment is held out; no threshold optimization on test data.
    n=len(rows); start=int(n*.60); test_end=n
    if n<300: return {"status":"INSUFFICIENT_DATA","candles":n,"minimum":300}
    c=[x["close"] for x in rows]; e50=ema(c,50); e200=ema(c,200); m=macd_series(c)
    pos=0; entry=0; trades=[]; equity=1.0; peak=1.0; maxdd=0.0
    # only enter/exit using signal from completed prior candle, execute next open
    for i in range(max(start+1,201),test_end):
        prev=i-1
        desired=1 if c[prev]>e50[prev]>e200[prev] and m[prev]>0 else -1 if c[prev]<e50[prev]<e200[prev] and m[prev]<0 else 0
        px=rows[i]["open"]
        if pos and desired!=pos:
            exit_px=px*(1-slip if pos==1 else 1+slip)
            gross=(exit_px/entry-1)*pos
            net=gross-2*fee
            equity*=max(0.000001,1+net)
            trades.append(net)
            peak=max(peak,equity); maxdd=max(maxdd,(peak-equity)/peak)
            pos=0
        if not pos and desired:
            entry=px*(1+slip if desired==1 else 1-slip); pos=desired
    if pos:
        px=rows[-1]["close"]*(1-slip if pos==1 else 1+slip)
        net=(px/entry-1)*pos-2*fee; equity*=max(.000001,1+net); trades.append(net)
        peak=max(peak,equity); maxdd=max(maxdd,(peak-equity)/peak)
    wins=[x for x in trades if x>0]; losses=[x for x in trades if x<=0]
    gross_win=sum(wins); gross_loss=abs(sum(losses))
    return {"status":"COMPLETED","method":"Chronological 60/20/20 split; reported trades only in final 20% holdout",
            "total_candles":n,"test_candles":n-start,"test_start_time":rows[start]["time"],
            "trades":len(trades),"win_rate_pct":round(len(wins)/len(trades)*100,2) if trades else None,
            "net_return_pct":round((equity-1)*100,2),"profit_factor":round(gross_win/gross_loss,3) if gross_loss else None,
            "max_drawdown_pct":round(maxdd*100,2),"fee_per_side_pct":fee*100,
            "slippage_per_side_pct":slip*100,"round_trip_cost_pct":(2*(fee+slip))*100,
            "warning":"Illustrative OHLCV simulation; funding, partial fills, liquidation and intrabar stop execution are not modeled."}

def macd_series(xs):
    a,b=ema(xs,12),ema(xs,26); line=[x-y for x,y in zip(a,b)]; sig=ema(line,9)
    return [x-y for x,y in zip(line,sig)]

def risk_calc(sig, equity):
    r=sig.get("risk_levels",{}); entry=r.get("entry_reference"); stop=r.get("stop_loss")
    lev=10
    if not equity or not entry or not stop or sig.get("signal") not in ("LONG","SHORT"):
        return {"status":"WAIT_OR_EQUITY_REQUIRED","leverage_cap":lev,"equity_usdt":equity,
                "note":"Set GitHub secret RISK_EQUITY_USDT for account-specific sizing. No balance is assumed."}
    risk_fraction=float(os.getenv("RISK_FRACTION","0.01")); risk_amt=equity*risk_fraction
    dist=abs(entry-stop); qty=risk_amt/dist if dist else 0; notional=qty*entry
    margin=notional/lev; loss_pct=dist/entry*100
    return {"status":"CALCULATED","leverage_cap":lev,"equity_usdt":equity,
            "risk_fraction_pct":risk_fraction*100,"max_planned_loss_usdt":round(risk_amt,2),
            "entry":entry,"stop":stop,"stop_distance_pct":round(loss_pct,4),
            "position_qty_btc":round(qty,6),"notional_usdt":round(notional,2),
            "estimated_initial_margin_usdt":round(margin,2),
            "effective_leverage":round(notional/equity,2),
            "note":"Sizing estimate, not an exchange liquidation-price calculation. Fees, funding, maintenance margin and slippage can increase loss."}

def main():
    market=load("market_data.json"); signal=load("signal_result.json")
    eq=float(os.getenv("RISK_EQUITY_USDT","0") or 0) or None
    tf={}
    for k,v in market.get("timeframes",{}).items():
        if v.get("ok"): tf[k]=indicators(v.get("candles",[]))
    rows=market.get("timeframes",{}).get("5m",{}).get("candles",[])
    result={"generated_at":datetime.now(timezone.utc).isoformat(),"timeframe_indicators":tf,
            "backtest_5m":backtest(rows),"risk_10x":risk_calc(signal,eq),
            "execution":"MANUAL_ONLY_NO_ORDERS"}
    with open("advanced_metrics.json","w",encoding="utf-8") as f: json.dump(result,f,indent=2)
    print(json.dumps({"indicators":list(tf),"backtest":result["backtest_5m"],"risk":result["risk_10x"]},indent=2))
if __name__=="__main__": main()
