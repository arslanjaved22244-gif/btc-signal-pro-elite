#!/usr/bin/env python3
"""Public GDELT news-event monitor. No API key; signal-only safety filter."""
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta

OUT = "event_guard.json"
STATE = "event_guard_state.json"
BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
RSS = "https://news.google.com/rss/search"
QUERY = '(bitcoin OR BTC OR cryptocurrency OR crypto OR markets OR Federal Reserve OR Trump) AND (Trump OR tariff OR Federal Reserve OR Fed OR inflation OR CPI OR NFP OR payroll OR FOMC OR Powell OR interest rate OR war OR attack OR missile OR Iran OR Israel OR Ukraine OR Russia OR China OR sanctions OR election OR central bank OR recession OR hack OR outage)'
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
    try:
        with open(STATE, "r", encoding="utf-8") as f:
            prior = json.load(f)
    except (OSError, ValueError):
        prior = {}

    prior_until = prior.get("block_until")
    result = {"generated_at": now.isoformat(), "source": "GDELT DOC public API",
              "available": False, "block": False, "block_until": None,
              "matched_events": [], "warnings": []}
    articles = []
    source_used = None
    errors = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 BTC-Signal-Pro-Elite/4.4"})
        with urllib.request.urlopen(req, timeout=15) as response:
            articles = json.loads(response.read().decode("utf-8")).get("articles", [])
        source_used = "GDELT DOC public API"
    except Exception as exc:
        errors.append("GDELT: " + str(exc))
    if source_used is None:
        # No-key RSS fallback when GDELT rate-limits public runners.
        try:
            rss_url = RSS + "?" + urllib.parse.urlencode({
                "q": "Bitcoin OR BTC OR cryptocurrency Trump Fed tariff inflation war Iran Israel Ukraine",
                "hl": "en-US", "gl": "US", "ceid": "US:en"
            })
            req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as response:
                root = ET.fromstring(response.read())
            for item in root.findall("./channel/item"):
                try:
                    published = parsedate_to_datetime(item.findtext("pubDate", "")).astimezone(timezone.utc)
                except Exception:
                    continue
                articles.append({"title": item.findtext("title", ""),
                                 "domain": urllib.parse.urlparse(item.findtext("link", "")).netloc,
                                 "published": published})
            source_used = "Google News RSS public fallback"
        except Exception as exc:
            errors.append("Google News RSS: " + str(exc))
    if source_used:
        result["available"] = True
        result["source"] = source_used
        for article in articles:
            title = str(article.get("title", ""))
            domain = str(article.get("domain", ""))
            if "published" in article:
                published = article["published"]
            else:
                seen = str(article.get("seendate", ""))
                try:
                    published = datetime.strptime(seen[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
            matched = [term for term in RISK_TERMS if term in title.lower()]
            if not matched:
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
    else:
        result["warnings"].append("News feeds unavailable: " + " | ".join(errors))

    fresh_until = None
    if result["matched_events"]:
        newest = max(datetime.fromisoformat(x["published_at"]) for x in result["matched_events"])
        fresh_until = (newest + timedelta(minutes=30)).isoformat()
    candidates = [x for x in (prior_until, fresh_until) if x]
    if candidates:
        try:
            active = [x for x in candidates if datetime.fromisoformat(x) > now]
            result["block_until"] = max(active, key=lambda x: datetime.fromisoformat(x)) if active else None
        except ValueError:
            result["warnings"].append("Invalid saved event-lock timestamp; ignoring it.")
    result["block"] = bool(result["block_until"])
    return result


if __name__ == "__main__":
    data = collect()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"block_until": data.get("block_until")}, f, indent=2)
    print(json.dumps({k: data[k] for k in ("available", "block", "block_until", "matched_events", "warnings")}, indent=2))
