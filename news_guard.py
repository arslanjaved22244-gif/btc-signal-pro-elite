#!/usr/bin/env python3
"""Public GDELT news-event monitor. No API key; signal-only safety filter."""
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

OUT = "event_guard.json"
BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
QUERY = '(bitcoin OR BTC OR cryptocurrency OR crypto) AND (Trump OR tariff OR Federal Reserve OR Fed OR inflation OR interest rate OR war OR attack OR missile OR Iran OR Israel OR Ukraine OR Russia OR China OR sanctions OR election OR central bank OR recession)'
RISK_TERMS = (
    "war", "attack", "missile", "strike", "invasion", "sanction", "emergency",
    "tariff", "trade war", "federal reserve", "interest rate", "rate decision",
    "inflation", "cpi", "fomc", "powell", "trump", "iran", "israel", "ukraine",
    "russia", "china", "explosion", "terror", "hack", "outage", "liquidation"
)


def collect():
    now = datetime.now(timezone.utc)
    params = {"query": QUERY, "mode": "ArtList", "format": "json",
              "maxrecords": "50", "sort": "DateDesc"}
    url = BASE + "?" + urllib.parse.urlencode(params)
    result = {"generated_at": now.isoformat(), "source": "GDELT DOC public API",
              "available": False, "block": False, "block_until": None,
              "matched_events": [], "warnings": []}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BTC-Signal-Pro-Elite/4.2"})
        with urllib.request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        articles = payload.get("articles", [])
        result["available"] = True
        for article in articles:
            title = str(article.get("title", ""))
            domain = str(article.get("domain", ""))
            seen = str(article.get("seendate", ""))
            text = title.lower()
            matched = [term for term in RISK_TERMS if term in text]
            if not matched:
                continue
            try:
                published = datetime.strptime(seen[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            age = (now - published).total_seconds()
            if age < -300 or age > 1800:
                continue
            result["matched_events"].append({
                "title": title[:300], "domain": domain,
                "published_at": published.isoformat(),
                "age_minutes": round(max(0, age) / 60, 1),
                "matched_terms": matched[:8]
            })
        if result["matched_events"]:
            result["block"] = True
            # Initial block is 30 minutes from latest qualifying report.
            newest = max(datetime.fromisoformat(x["published_at"]) for x in result["matched_events"])
            result["block_until"] = (newest + timedelta(minutes=30)).isoformat()
    except Exception as exc:
        result["warnings"].append("News feed unavailable: " + str(exc))
    return result


if __name__ == "__main__":
    data = collect()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(json.dumps({k: data[k] for k in ("available", "block", "block_until", "matched_events", "warnings")}, indent=2))
