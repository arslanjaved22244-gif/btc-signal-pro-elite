#!/usr/bin/env python3
"""BTC Signal Pro Elite: scheduled, signal-only Binance Futures monitor."""
import json, os, sys, time, math, urllib.request, urllib.parse, xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

BASE = "https://fapi.binance.com"
STATE_FILE = "state.json"
INTERVALS = {"5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
NEWS_PAUSE_MIN = 30
NEWS_MAX_AGE_MIN = 45

def utcnow():
    return datetime.now(timezone.utc)

def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "BTC-Signal-Pro-Elite/1.0"})
    with urllib.request.urlopen(req, timeout=15) as res:
        return json.loads(res.read().decode("utf-8"))

def http_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 BTC-Signal-Pro-Elite"})
    with urllib.request.urlopen(req, timeout=15) as res:
        return res.read().decode("utf-8", "replace")

def ema(values, period):
    if len(values) < period: return None
    k = 2.0 / (period + 1)
    out = sum(values[:period]) / period
    for v in values[period:]: out = v * k + out * (1-k)
    return out

def rsi(values, period=14):
    if len(values) < period + 1: return None
    gains, losses = [], []
    for i in range(1, len(values)):
        d = values[i] - values[i-1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    ag = sum(gains[:period]) / period; al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag*(period-1)+gains[i])/period
        al = (al*(period-1)+losses[i])/period
    if al == 0: return 100.0
    return 100 - 100/(1 + ag/al)

def atr(klines, period=14):
    if len(klines) < period + 1: return None
    trs = []
    for i in range(1, len(klines)):
        hi, lo, prev = float(klines[i][2]), float(klines[i][3]), float(klines[i-1][4])
        trs.append(max(hi-lo, abs(hi-prev), abs(lo-prev)))
    return sum(trs[-period:]) / period

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f: json.dump(state, f)

def telegram(message):
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram not configured: add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID repository secrets.")
        return False
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request("https://api.telegram.org/bot" + token + "/sendMessage", data=data)
    with urllib.request.urlopen(req, timeout=15) as res:
        result = json.loads(res.read().decode())
    return bool(result.get("ok"))

def check_news():
    queries = [
        '(Bitcoin OR crypto) (Trump OR tariff OR war OR Iran OR Israel OR sanctions OR Fed OR FOMC OR hack OR SEC)',
        'Trump Bitcoin crypto market statement'
    ]
    now = utcnow()
    hits = []
    for q in queries:
        url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=en-US&gl=US&ceid=US:en"
        try:
            root = ET.fromstring(http_text(url))
            for item in root.findall(".//item"):
                title = item.findtext("title") or ""
                pub = item.findtext("pubDate") or ""
                try: dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
                except Exception: continue
                age = (now-dt).total_seconds()/60
                low = title.lower()
                relevant = any(k in low for k in ["bitcoin","crypto","trump","tariff","war","iran","israel","sanction","federal reserve","fed ","fomc","hack","sec ","exchange","etf"])
                if relevant and -2 <= age <= NEWS_MAX_AGE_MIN:
                    hits.append({"title": title, "published": dt.isoformat(), "age": round(age,1)})
        except Exception as e:
            print("News feed warning:", str(e)[:180])
    unique = {h["title"]: h for h in hits}
    return sorted(unique.values(), key=lambda x: x["age"])

def market_snapshot():
    price = float(http_json(BASE + "/fapi/v1/ticker/price?symbol=BTCUSDT")["price"])
    data = {}
    for tf, interval in INTERVALS.items():
        kl = http_json(BASE + "/fapi/v1/klines?symbol=BTCUSDT&interval=" + interval + "&limit=240")
        closes = [float(x[4]) for x in kl]
        data[tf] = {
            "close": closes[-1], "ema50": ema(closes,50), "ema200": ema(closes,200),
            "rsi": rsi(closes), "atr": atr(kl), "klines": kl
        }
    funding = float(http_json(BASE + "/fapi/v1/premiumIndex?symbol=BTCUSDT")["lastFundingRate"])
    oi = float(http_json(BASE + "/fapi/v1/openInterest?symbol=BTCUSDT")["openInterest"])
    oi_hist = http_json(BASE + "/futures/data/openInterestHist?symbol=BTCUSDT&period=5m&limit=2")
    oi_change = None
    if len(oi_hist) >= 2:
        a,b = float(oi_hist[-2]["sumOpenInterest"]), float(oi_hist[-1]["sumOpenInterest"])
        oi_change = (b-a)/a*100 if a else 0
    book = http_json(BASE + "/fapi/v1/depth?symbol=BTCUSDT&limit=100")
    bids = sum(float(p)*float(q) for p,q in book["bids"][:20])
    asks = sum(float(p)*float(q) for p,q in book["asks"][:20])
    imbalance = (bids-asks)/(bids+asks) if bids+asks else 0
    trades = http_json(BASE + "/fapi/v1/aggTrades?symbol=BTCUSDT&limit=500")
    buy = sell = 0.0
    for t in trades:
        qty = float(t["q"])
        if t["m"]: sell += qty
        else: buy += qty
    cvd_ratio = (buy-sell)/(buy+sell) if buy+sell else 0
    last5 = data["5m"]["klines"][-1]
    taker_buy_ratio = float(last5[9])/float(last5[5]) if float(last5[5]) else 0.5
    return price, data, funding, oi, oi_change, imbalance, cvd_ratio, taker_buy_ratio

def build_signal(data, funding, oi_change, imbalance, cvd, taker):
    # Higher-timeframe trend votes; use only completed candle history returned by Binance.
    votes = 0
    for tf in ("15m","1h","4h","1d"):
        d = data[tf]
        if d["ema50"] is not None and d["ema200"] is not None:
            votes += 1 if d["close"] > d["ema50"] > d["ema200"] else (-1 if d["close"] < d["ema50"] < d["ema200"] else 0)
    r = data["15m"]["rsi"]
    long_flow = cvd > 0.08 and taker > 0.53 and imbalance > 0.03
    short_flow = cvd < -0.08 and taker < 0.47 and imbalance < -0.03
    # Funding/OI are crowding and squeeze-risk filters, not standalone entry signals.
    long_crowded = funding > 0.0005 and oi_change is not None and oi_change > 0.5
    short_crowded = funding < -0.0005 and oi_change is not None and oi_change > 0.5
    if votes >= 2 and long_flow and r is not None and 48 <= r <= 68 and not long_crowded:
        return "LONG", "HTF trend + positive taker/CVD/order-book confirmation"
    if votes <= -2 and short_flow and r is not None and 32 <= r <= 52 and not short_crowded:
        return "SHORT", "HTF downtrend + negative taker/CVD/order-book confirmation"
    return "WAIT", "Trend, flow, RSI or squeeze-risk filters are not aligned"

def main():
    state = load_state()
    try:
        price, data, funding, oi, oi_change, imbalance, cvd, taker = market_snapshot()
        news = check_news()
        now = utcnow()
        pause_until = state.get("pause_until")
        if news:
            proposed = now + timedelta(minutes=NEWS_PAUSE_MIN)
            try: old = datetime.fromisoformat(pause_until) if pause_until else now
            except Exception: old = now
            pause_until = max(old, proposed).isoformat()
            state["pause_until"] = pause_until
            state["pause_reason"] = news[0]["title"]
        paused = False
        if pause_until:
            try: paused = now < datetime.fromisoformat(pause_until)
            except Exception: paused = False
        signal, reason = build_signal(data, funding, oi_change, imbalance, cvd, taker)
        if paused: signal, reason = "WAIT", "NEWS SAFETY PAUSE until " + pause_until
        atr15 = data["15m"]["atr"] or 0
        entry = price
        if signal == "LONG" and atr15:
            sl, t1, t2, t3 = price-1.5*atr15, price+3*atr15, price+4.5*atr15, price+6*atr15
        elif signal == "SHORT" and atr15:
            sl, t1, t2, t3 = price+1.5*atr15, price-3*atr15, price-4.5*atr15, price-6*atr15
        else: sl=t1=t2=t3=None
        stamp = now.strftime("%Y-%m-%d %H:%M UTC")
        msg = [
            "BTC/USDT • " + signal, stamp, "Price: $" + format(price,",.2f"),
            "Reason: " + reason,
            "Funding: " + format(funding*100, ".4f") + "% | OI: " + format(oi,",.2f"),
            "OI change (5m): " + (format(oi_change,"+.2f")+"%" if oi_change is not None else "n/a"),
            "CVD proxy: " + format(cvd*100,"+.1f") + "% | Taker buy: " + format(taker*100,".1f") + "%",
            "Order-book imbalance: " + format(imbalance*100,"+.1f") + "%"
        ]
        if sl is not None:
            msg += ["Entry: " + format(entry,",.2f"), "Stop: " + format(sl,",.2f"),
                    "TP1/2/3: " + " / ".join(format(x,",.2f") for x in (t1,t2,t3)),
                    "Risk note: manual trading only; max leverage guidance 10x. Leverage magnifies losses."]
        if news:
            msg += ["NEWS PAUSE: " + news[0]["title"]]
        message = "\n".join(msg)
        print(message)
        previous = state.get("last_alert_key")
        # Notify on a signal change; WAIT is sent only when it replaces an active signal.
        key = signal + "|" + (reason if signal != "WAIT" else ("PAUSE" if paused else "WAIT"))
        if key != previous and os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
            telegram(message)
        state["last_alert_key"] = key
        state["last_check"] = now.isoformat()
        state["last_price"] = price
        save_state(state)
    except Exception as e:
        print("Monitor error:", repr(e))
        # Fail the workflow so GitHub Actions visibly reports a failed check.
        raise

if __name__ == "__main__":
    main()
