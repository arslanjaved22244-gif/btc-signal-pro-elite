#!/usr/bin/env python3
"""
BTC Signal Pro Elite - Advanced Multi-Timeframe ML Engine.
Public exchange OHLCV only; no API keys and no orders are placed.
"""
import bisect
import csv
import io
import zipfile
import calendar
import json
import math
import time
import urllib.request
from datetime import datetime, timezone

SYMBOL = "BTC/USDT"
DATA_SOURCE = "Binance USD-M Futures public klines"
INTERVALS = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
FUTURE_CANDLES = 3
MOVE_THRESHOLD = 0.0005
OUTPUT_FILE = "ai_result.json"
CLASSES = ("SHORT", "WAIT", "LONG")


def get_binance_data(interval):
    """Load historical Binance USD-M Futures monthly kline archives only."""
    now = datetime.now(timezone.utc)
    months = {"5m": 2, "15m": 3, "1h": 4, "4h": 8, "1d": 15}[interval]
    candles_by_time = {}
    year, month = now.year, now.month
    for _ in range(months):
        last_day = calendar.monthrange(year, month)[1]
        month_tag = f"{year}-{month:02d}"
        url = (
            "https://data.binance.vision/data/futures/um/monthly/klines/"
            f"BTCUSDT/{interval}/BTCUSDT-{interval}-{month_tag}.zip"
        )
        request = urllib.request.Request(
            url, headers={"User-Agent": "BTC-Signal-Pro-Elite/4.3"}
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                archive = zipfile.ZipFile(io.BytesIO(response.read()))
            with archive.open(archive.namelist()[0]) as csv_file:
                reader = csv.reader(io.TextIOWrapper(csv_file, encoding="utf-8"))
                for row in reader:
                    if not row or not row[0].isdigit():
                        continue
                    timestamp = int(row[0])
                    close_time = int(row[6]) if len(row) > 6 else timestamp
                    if close_time >= int(now.timestamp() * 1000):
                        continue
                    candles_by_time[timestamp] = {
                        "time": timestamp, "open": float(row[1]),
                        "high": float(row[2]), "low": float(row[3]),
                        "close": float(row[4]), "volume": float(row[5])
                    }
        except Exception as exc:
            # The current month's archive may not yet be published; older months
            # are still required to reach the minimum training history.
            print(f"Futures archive unavailable for {month_tag}: {exc}")
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    # Monthly archives can lag during the current month. Always merge the
    # latest public USD-M Futures REST candles so forecasts use current data.
    recent_url = (
        "https://fapi.binance.com/fapi/v1/klines?"
        f"symbol=BTCUSDT&interval={interval}&limit=1500"
    )
    recent_request = urllib.request.Request(
        recent_url, headers={"User-Agent": "BTC-Signal-Pro-Elite/5.0"}
    )
    try:
        with urllib.request.urlopen(recent_request, timeout=30) as response:
            recent_rows = json.loads(response.read().decode("utf-8"))
        if not isinstance(recent_rows, list):
            raise ValueError(f"Unexpected Binance Futures klines response: {recent_rows}")
        for row in recent_rows:
            timestamp = int(row[0])
            close_time = int(row[6])
            if close_time >= int(now.timestamp() * 1000):
                continue
            candles_by_time[timestamp] = {
                "time": timestamp, "open": float(row[1]),
                "high": float(row[2]), "low": float(row[3]),
                "close": float(row[4]), "volume": float(row[5])
            }
    except Exception as exc:
        print(f"Latest Binance Futures REST candles unavailable for {interval}: {exc}")
    candles = [candles_by_time[t] for t in sorted(candles_by_time)]
    if len(candles) < 210:
        raise ValueError(
            f"Insufficient Binance USD-M Futures candles for {interval}: {len(candles)}"
        )
    max_age_ms = max(15 * 60 * 1000, INTERVALS[interval] * 60 * 1000 * 2)
    age_ms = int(now.timestamp() * 1000) - candles[-1]["time"]
    if age_ms > max_age_ms:
        raise ValueError(
            f"STALE DATA BLOCK: latest {interval} Futures candle is "
            f"{age_ms / 60000:.1f} minutes old; maximum allowed is {max_age_ms / 60000:.1f} minutes"
        )
    return candles

def get_binance_spot_data(interval):
    # Public Binance Spot klines are a fallback when Futures is region-blocked.
    candles_by_time = {}
    end_time = None
    for _ in range(3):
        query = f"symbol=BTCUSDT&interval={interval}&limit=1000"
        if end_time is not None:
            query += f"&endTime={end_time}"
        url = "https://api.binance.com/api/v3/klines?" + query
        request = urllib.request.Request(
            url, headers={"User-Agent": "BTC-Signal-Pro-Elite/3.1"}
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            rows = json.loads(response.read().decode("utf-8"))
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            candles_by_time[int(row[0])] = {
                "time": int(row[0]), "open": float(row[1]),
                "high": float(row[2]), "low": float(row[3]),
                "close": float(row[4]), "volume": float(row[5])
            }
        oldest = min(int(row[0]) for row in rows)
        if len(rows) < 1000 or oldest <= 0:
            break
        end_time = oldest - 1
    candles = [candles_by_time[t] for t in sorted(candles_by_time)]
    candles = candles[:-1]  # discard the currently forming candle
    if len(candles) < 210:
        raise ValueError(f"Not enough completed Binance Spot {interval} candles: {len(candles)}")
    return candles


def get_coinbase_data(granularity_seconds, max_candles):
    # Coinbase Exchange public candles: max 300 buckets per request, no API key.
    candles_by_time = {}
    remaining = max_candles
    end_time = int(datetime.now(timezone.utc).timestamp())
    while remaining > 0:
        batch = min(300, remaining)
        start_time = end_time - (batch - 1) * granularity_seconds
        start_iso = datetime.fromtimestamp(start_time, timezone.utc).isoformat()
        end_iso = datetime.fromtimestamp(end_time, timezone.utc).isoformat()
        url = (
            "https://api.exchange.coinbase.com/products/BTC-USD/candles"
            f"?granularity={granularity_seconds}&start={start_iso}&end={end_iso}"
        )
        request = urllib.request.Request(
            url, headers={"User-Agent": "BTC-Signal-Pro-Elite/3.2", "Accept": "application/json"}
        )
        last_error = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    rows = json.loads(response.read().decode("utf-8"))
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
        if last_error is not None:
            raise RuntimeError(f"Coinbase candles request failed: {last_error}")
        if not isinstance(rows, list):
            raise ValueError(f"Unexpected Coinbase response: {rows}")
        for row in rows:
            # Coinbase format: [time, low, high, open, close, volume].
            timestamp = int(row[0]) * 1000
            candles_by_time[timestamp] = {
                "time": timestamp, "open": float(row[3]),
                "high": float(row[2]), "low": float(row[1]),
                "close": float(row[4]), "volume": float(row[5])
            }
        remaining -= batch
        end_time = start_time - 1
        if not rows:
            break
    candles = [candles_by_time[t] for t in sorted(candles_by_time)]
    candles = candles[:-1]  # discard the currently forming candle
    if len(candles) < 210:
        raise ValueError(
            f"Not enough completed Coinbase candles at {granularity_seconds}s: {len(candles)}"
        )
    return candles


def aggregate_four_hour(hourly_candles):
    buckets = {}
    for candle in hourly_candles:
        bucket = (candle["time"] // 14_400_000) * 14_400_000
        buckets.setdefault(bucket, []).append(candle)
    result = []
    for bucket in sorted(buckets):
        group = sorted(buckets[bucket], key=lambda item: item["time"])
        if len(group) != 4:
            continue
        result.append({
            "time": bucket, "open": group[0]["open"],
            "high": max(item["high"] for item in group),
            "low": min(item["low"] for item in group),
            "close": group[-1]["close"],
            "volume": sum(item["volume"] for item in group)
        })
    if len(result) < 210:
        raise ValueError(f"Not enough complete Coinbase 4h candles: {len(result)}")
    return result


def get_kraken_data(interval_minutes):
    # Geographic restrictions can block Binance Futures API requests on CI runners.
    url = f"https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval={interval_minutes}"
    request = urllib.request.Request(url, headers={"User-Agent": "BTC-Signal-Pro-Elite/3.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = json.loads(response.read().decode("utf-8"))
    if raw.get("error"):
        raise RuntimeError("Kraken API error: " + ", ".join(raw["error"]))
    result = raw.get("result", {})
    pair_key = next((key for key in result if key != "last"), None)
    if pair_key is None:
        raise ValueError(f"Kraken returned no BTC/USD candles for {interval_minutes}m.")
    candles = [{
        "time": int(float(row[0]) * 1000), "open": float(row[1]),
        "high": float(row[2]), "low": float(row[3]),
        "close": float(row[4]), "volume": float(row[6])
    } for row in result[pair_key]]
    candles = candles[:-1]
    if len(candles) < 210:
        raise ValueError(f"Not enough completed Kraken {interval_minutes}m candles: {len(candles)}")
    return candles


def ema(values, period):
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1.0 - alpha) * out[-1])
    return out


def rsi(values, period=14):
    if len(values) <= period:
        return 50.0
    gains, losses = [], []
    for a, b in zip(values[:-1], values[1:]):
        change = b - a
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss <= 1e-15:
        return 100.0 if avg_gain > 1e-15 else 50.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def safe_return(values, period):
    if len(values) <= period or values[-1 - period] == 0:
        return 0.0
    return values[-1] / values[-1 - period] - 1.0


def candle_features(candles, index):
    subset = candles[:index + 1]
    closes = [c["close"] for c in subset]
    volumes = [c["volume"] for c in subset]
    c = candles[index]
    price = c["close"]
    if price <= 0 or len(closes) < 200:
        return None
    e9, e21, e50, e200 = (ema(closes, p)[-1] for p in (9, 21, 50, 200))
    avg_vol = sum(volumes[-20:]) / min(20, len(volumes))
    span = max(c["high"] - c["low"], 0.0)
    return [
        safe_return(closes, 1), safe_return(closes, 3),
        safe_return(closes, 6), safe_return(closes, 12),
        safe_return(closes, 24), rsi(closes) / 100.0,
        e9 / e21 - 1.0 if e21 else 0.0,
        e21 / e50 - 1.0 if e50 else 0.0,
        e50 / e200 - 1.0 if e200 else 0.0,
        price / e200 - 1.0 if e200 else 0.0,
        min(c["volume"] / avg_vol, 5.0) if avg_vol > 0 else 1.0,
        span / price, (c["close"] - c["open"]) / price,
        (c["close"] - c["low"]) / span if span > 0 else 0.5
    ]


def latest_closed_index(candles, timestamp, interval_minutes):
    cutoff = timestamp - interval_minutes * 60_000
    times = [c["time"] for c in candles]
    return bisect.bisect_right(times, cutoff) - 1


def precompute_candle_features(candles):
    # Calculate indicators once per candle instead of recalculating the full
    # history for every training row. This makes longer 5m history practical.
    n = len(candles)
    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]
    ema_periods = (9, 21, 50, 200)
    ema_series = {}
    for period in ema_periods:
        alpha = 2.0 / (period + 1.0)
        values = [closes[0]]
        for price in closes[1:]:
            values.append(alpha * price + (1.0 - alpha) * values[-1])
        ema_series[period] = values

    rsi_values = [50.0] * n
    if n > 14:
        changes = [closes[i] - closes[i - 1] for i in range(1, n)]
        avg_gain = sum(max(v, 0.0) for v in changes[:14]) / 14.0
        avg_loss = sum(max(-v, 0.0) for v in changes[:14]) / 14.0
        def rsi_value(gain, loss):
            if loss <= 1e-15:
                return 100.0 if gain > 1e-15 else 50.0
            return 100.0 - 100.0 / (1.0 + gain / loss)
        rsi_values[14] = rsi_value(avg_gain, avg_loss)
        for i in range(15, n):
            change = changes[i - 1]
            avg_gain = (avg_gain * 13.0 + max(change, 0.0)) / 14.0
            avg_loss = (avg_loss * 13.0 + max(-change, 0.0)) / 14.0
            rsi_values[i] = rsi_value(avg_gain, avg_loss)

    result = [None] * n
    volume_sum = 0.0
    for i, candle in enumerate(candles):
        volume_sum += volumes[i]
        if i >= 20:
            volume_sum -= volumes[i - 20]
        if i < 199:
            continue
        price = closes[i]
        if price <= 0:
            continue
        e9, e21 = ema_series[9][i], ema_series[21][i]
        e50, e200 = ema_series[50][i], ema_series[200][i]
        avg_vol = volume_sum / min(i + 1, 20)
        span = max(candle["high"] - candle["low"], 0.0)
        def ret(period):
            return closes[i] / closes[i - period] - 1.0 if i >= period and closes[i - period] else 0.0
        result[i] = [
            ret(1), ret(3), ret(6), ret(12), ret(24), rsi_values[i] / 100.0,
            e9 / e21 - 1.0 if e21 else 0.0,
            e21 / e50 - 1.0 if e50 else 0.0,
            e50 / e200 - 1.0 if e200 else 0.0,
            price / e200 - 1.0 if e200 else 0.0,
            min(volumes[i] / avg_vol, 5.0) if avg_vol > 0 else 1.0,
            span / price, (candle["close"] - candle["open"]) / price,
            (candle["close"] - candle["low"]) / span if span > 0 else 0.5
        ]
    return result


def combined_features(data, base_index, prepared):
    base = data["5m"]
    timestamp = base[base_index]["time"]
    features = prepared["5m"][base_index]
    if features is None:
        return None
    features = list(features)
    for tf in ("15m", "1h", "4h", "1d"):
        idx = latest_closed_index(data[tf], timestamp, INTERVALS[tf])
        if idx < 199:
            return None
        values = prepared[tf][idx]
        if values is None:
            return None
        features.extend([
            values[0], values[2], values[5], values[6],
            values[7], values[8], values[9], values[10]
        ])
    return features


def adaptive_move_threshold(candles, index, horizon_candles=FUTURE_CANDLES):
    """Scale the neutral band to recent realized volatility; use past candles only."""
    price = candles[index]["close"]
    if price <= 0:
        return MOVE_THRESHOLD
    atr_pct = candle_atr(candles, index, 14) / price
    horizon_scale = math.sqrt(max(1, horizon_candles) / FUTURE_CANDLES)
    return max(0.00035, atr_pct * 0.20 * horizon_scale)


def build_dataset(data, prepared):
    candles = data["5m"]
    rows, labels = [], []
    for i in range(200, len(candles) - FUTURE_CANDLES):
        features = combined_features(data, i, prepared)
        if features is None:
            continue
        move = candles[i + FUTURE_CANDLES]["close"] / candles[i]["close"] - 1.0
        threshold = adaptive_move_threshold(candles, i, FUTURE_CANDLES)
        label = 2 if move > threshold else 0 if move < -threshold else 1
        rows.append(features)
        labels.append(label)
    return rows, labels


def softmax(scores):
    peak = max(scores)
    exps = [math.exp(max(-40.0, min(40.0, score - peak))) for score in scores]
    total = sum(exps)
    return [value / total for value in exps]


def fit_scaler(rows):
    means, scales = [], []
    for j in range(len(rows[0])):
        column = [row[j] for row in rows]
        mean = sum(column) / len(column)
        variance = sum((value - mean) ** 2 for value in column) / len(column)
        means.append(mean)
        scales.append(math.sqrt(variance) or 1.0)
    return means, scales


def train_model(rows, labels, epochs=140, learning_rate=0.025, l2=0.01):
    means, scales = fit_scaler(rows)
    x = [[(v - means[j]) / scales[j] for j, v in enumerate(row)] for row in rows]
    n, p = len(x), len(x[0])
    # Balanced inverse-frequency weights preserve all three classes.
    # Model 3.3's WAIT down-weight caused near-exclusive SHORT predictions.
    class_counts = [max(1, labels.count(k)) for k in range(len(CLASSES))]
    class_weights = [
        min(2.5, n / (len(CLASSES) * count))
        for count in class_counts
    ]
    # Exponential recency weighting helps the model adapt to changing
    # market regimes while retaining older samples for context.
    recency_half_life = 1800.0
    sample_weights = [
        math.exp(-math.log(2.0) * (n - 1 - i) / recency_half_life)
        for i in range(n)
    ]
    mean_sample_weight = sum(sample_weights) / n
    sample_weights = [value / mean_sample_weight for value in sample_weights]
    weights = [[0.0] * p for _ in CLASSES]
    biases = [0.0] * len(CLASSES)
    for _ in range(epochs):
        grad_w = [[0.0] * p for _ in CLASSES]
        grad_b = [0.0] * len(CLASSES)
        for row, actual, recency_weight in zip(x, labels, sample_weights):
            probs = softmax([
                biases[k] + sum(weights[k][j] * row[j] for j in range(p))
                for k in range(len(CLASSES))
            ])
            for k in range(len(CLASSES)):
                error = (
                    (probs[k] - (1.0 if k == actual else 0.0))
                    * class_weights[actual] * recency_weight
                )
                grad_b[k] += error
                for j in range(p):
                    grad_w[k][j] += error * row[j]
        for k in range(len(CLASSES)):
            biases[k] -= learning_rate * grad_b[k] / n
            for j in range(p):
                weights[k][j] -= learning_rate * (grad_w[k][j] / n + l2 * weights[k][j])
    return {"weights": weights, "biases": biases, "means": means, "scales": scales}


def predict(model, row):
    x = [(value - model["means"][j]) / model["scales"][j] for j, value in enumerate(row)]
    return softmax([
        model["biases"][k] + sum(model["weights"][k][j] * x[j] for j in range(len(x)))
        for k in range(len(CLASSES))
    ])



def train_centroid(rows, labels):
    means, scales = fit_scaler(rows)
    sums = [[0.0] * len(means) for _ in CLASSES]
    counts = [0] * len(CLASSES)
    for row, label in zip(rows, labels):
        counts[label] += 1
        for j, value in enumerate(row): sums[label][j] += (value-means[j])/scales[j]
    centers = [[v/max(1,counts[k]) for v in sums[k]] for k in range(3)]
    return {"means":means,"scales":scales,"centers":centers}

def predict_centroid(model, row):
    x=[(v-model["means"][j])/model["scales"][j] for j,v in enumerate(row)]
    distances=[sum((a-b)**2 for a,b in zip(x,c))/len(x) for c in model["centers"]]
    return softmax([-d/0.08 for d in distances])

def train_stumps(rows, labels, max_stumps=48):
    means,scales=fit_scaler(rows)
    x=[[(v-means[j])/scales[j] for j,v in enumerate(row)] for row in rows]
    n,p=len(x),len(x[0]); majority=max(labels.count(k) for k in range(3))/n
    candidates=[]
    def leaf(indices):
        counts=[1.0,1.0,1.0]
        for i in indices: counts[labels[i]]+=1.0
        total=sum(counts)
        return [v/total for v in counts]
    for j in range(p):
        col=sorted(row[j] for row in x)
        for q in (0.25,0.5,0.75):
            threshold=col[min(n-1,int((n-1)*q))]
            left=[i for i,row in enumerate(x) if row[j]<=threshold]
            right=[i for i,row in enumerate(x) if row[j]>threshold]
            if len(left)<12 or len(right)<12: continue
            lp,rp=leaf(left),leaf(right)
            correct=sum(1 for i in left if labels[i]==max(range(3),key=lambda k:lp[k]))
            correct+=sum(1 for i in right if labels[i]==max(range(3),key=lambda k:rp[k]))
            score=correct/n
            if score>majority+0.005: candidates.append((score,j,threshold,lp,rp))
    candidates.sort(key=lambda z:z[0],reverse=True)
    chosen=candidates[:max_stumps]
    if not chosen:
        global_probs=[(labels.count(k)+1)/(n+3) for k in range(3)]
        chosen=[(majority,0,0.0,global_probs,global_probs)]
    return {"means":means,"scales":scales,"stumps":[{"feature":j,"threshold":t,"left":lp,"right":rp,"weight":max(0.005,score-majority)} for score,j,t,lp,rp in chosen]}

def predict_stumps(model,row):
    x=[(v-model["means"][j])/model["scales"][j] for j,v in enumerate(row)]
    totals=[0.0,0.0,0.0]; weight_sum=0.0
    for stump in model["stumps"]:
        w=stump["weight"]; side=stump["left"] if x[stump["feature"]]<=stump["threshold"] else stump["right"]
        for k in range(3): totals[k]+=w*side[k]
        weight_sum+=w
    return [v/max(weight_sum,1e-9) for v in totals]

def train_ensemble(rows, labels):
    full_model=train_model(rows,labels)
    recent_window=min(3000,len(rows))
    recent_model=train_model(rows[-recent_window:],labels[-recent_window:])
    centroid_model=train_centroid(rows,labels)
    stump_model=train_stumps(rows,labels)
    return {"full_history":full_model,"recent_regime":recent_model,"centroid":centroid_model,"stumps":stump_model,"recent_window":recent_window}

def predict_ensemble(ensemble,row):
    a=predict(ensemble["full_history"],row)
    b=predict(ensemble["recent_regime"],row)
    c=predict_centroid(ensemble["centroid"],row)
    d=predict_stumps(ensemble["stumps"],row)
    return [0.35*a[k]+0.20*b[k]+0.20*c[k]+0.25*d[k] for k in range(3)]


def classification_metrics(actual, predicted):
    matrix = [[0 for _ in CLASSES] for _ in CLASSES]
    for a, p in zip(actual, predicted):
        matrix[a][p] += 1
    per_class, precisions, recalls, f1s = {}, [], [], []
    for k, name in enumerate(CLASSES):
        tp = matrix[k][k]
        fp = sum(matrix[i][k] for i in range(3) if i != k)
        fn = sum(matrix[k][j] for j in range(3) if j != k)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[name] = {
            "precision": round(precision * 100, 2),
            "recall": round(recall * 100, 2),
            "f1": round(f1 * 100, 2),
            "support": sum(matrix[k])
        }
        precisions.append(precision)
        recalls.append(recall)
        f1s.append(f1)
    accuracy = sum(matrix[i][i] for i in range(3)) / max(1, len(actual))
    return {
        "accuracy": round(accuracy * 100, 2),
        "macro_precision": round(sum(precisions) / 3 * 100, 2),
        "macro_recall": round(sum(recalls) / 3 * 100, 2),
        "macro_f1": round(sum(f1s) / 3 * 100, 2),
        "per_class": per_class,
        "confusion_matrix": matrix
    }


def classify_feature_regime(row):
    # Regime is inferred only from features available at the forecast timestamp.
    slope = row[7] if len(row) > 7 else 0.0
    ema_spread = row[8] if len(row) > 8 else 0.0
    price_vs_ema200 = row[9] if len(row) > 9 else 0.0
    if ema_spread > 0.001 and price_vs_ema200 > 0 and slope > 0:
        return "BULL_TREND"
    if ema_spread < -0.001 and price_vs_ema200 < 0 and slope < 0:
        return "BEAR_TREND"
    if abs(ema_spread) < 0.0015 and abs(slope) < 0.0015:
        return "RANGE_CHOP"
    return "TRANSITION"


def predict_with_wait_threshold(probs, wait_threshold):
    # Keep WAIT as a first-class outcome instead of allowing directional
    # classes to dominate whenever their scores are only marginally higher.
    if probs[1] >= wait_threshold:
        return 1
    return 0 if probs[0] >= probs[2] else 2


def tune_wait_threshold(rows, labels):
    # Tune only on a chronological calibration tail inside the training data.
    # The outer walk-forward test window is never used to choose this value.
    n = len(rows)
    split = int(n * 0.80)
    calibration_start = split + FUTURE_CANDLES
    if split < 150 or calibration_start >= n - 30:
        return 0.30
    calibration_model = train_ensemble(rows[:split], labels[:split])
    actual = labels[calibration_start:]
    probability_rows = [
        predict_ensemble(calibration_model, row) for row in rows[calibration_start:]
    ]
    best_threshold, best_f1 = 0.30, -1.0
    for step in range(9):
        threshold = 0.20 + step * 0.05
        predicted = [
            predict_with_wait_threshold(probs, threshold)
            for probs in probability_rows
        ]
        score = classification_metrics(actual, predicted)["macro_f1"]
        if score > best_f1:
            best_f1 = score
            best_threshold = threshold
    return best_threshold


def walk_forward_validate(rows, labels):
    n = len(rows)
    test_size = n // 6
    if test_size < 25:
        raise ValueError("Not enough samples for three walk-forward test windows.")
    actual_all, predicted_all, regime_all, fold_results = [], [], [], []
    baseline_correct = 0
    baseline_total = 0
    for fold in range(3):
        test_start = n // 2 + fold * test_size
        test_end = min(n, test_start + test_size)
        train_end = test_start - FUTURE_CANDLES
        if train_end < 150 or test_end <= test_start:
            continue
        train_labels = labels[:train_end]
        wait_threshold = tune_wait_threshold(rows[:train_end], train_labels)
        model = train_ensemble(rows[:train_end], train_labels)
        fold_actual, fold_predicted, fold_regimes = [], [], []
        majority_label = max(range(len(CLASSES)), key=lambda k: train_labels.count(k))
        for row, actual in zip(rows[test_start:test_end], labels[test_start:test_end]):
            probs = predict_ensemble(model, row)
            fold_actual.append(actual)
            fold_predicted.append(predict_with_wait_threshold(probs, wait_threshold))
            fold_regimes.append(classify_feature_regime(row))
        fold_metrics = classification_metrics(fold_actual, fold_predicted)
        fold_baseline = sum(1 for actual in fold_actual if actual == majority_label)
        baseline_correct += fold_baseline
        baseline_total += len(fold_actual)
        fold_regime_metrics = {}
        for regime in ("BULL_TREND", "BEAR_TREND", "RANGE_CHOP", "TRANSITION"):
            indices = [i for i, value in enumerate(fold_regimes) if value == regime]
            if indices:
                fold_regime_metrics[regime] = classification_metrics(
                    [fold_actual[i] for i in indices],
                    [fold_predicted[i] for i in indices]
                )
        fold_results.append({
            "fold": fold + 1, "train_samples": train_end,
            "test_samples": len(fold_actual),
            "wait_threshold": wait_threshold,
            "train_majority_class": CLASSES[majority_label],
            "majority_baseline_accuracy": round(fold_baseline / len(fold_actual) * 100, 2),
            "metrics": fold_metrics,
            "regime_metrics": fold_regime_metrics
        })
        actual_all.extend(fold_actual)
        predicted_all.extend(fold_predicted)
        regime_all.extend(fold_regimes)
    if not actual_all:
        raise ValueError("Walk-forward validation could not create a valid fold.")
    aggregate = classification_metrics(actual_all, predicted_all)
    aggregate["majority_class_baseline_pct"] = round(
        baseline_correct / max(1, baseline_total) * 100, 2
    )
    aggregate["balanced_random_baseline_pct"] = round(100 / len(CLASSES), 2)
    aggregate["regime_breakdown"] = {}
    for regime in ("BULL_TREND", "BEAR_TREND", "RANGE_CHOP", "TRANSITION"):
        indices = [i for i, value in enumerate(regime_all) if value == regime]
        if indices:
            aggregate["regime_breakdown"][regime] = classification_metrics(
                [actual_all[i] for i in indices],
                [predicted_all[i] for i in indices]
            )
    return aggregate, fold_results


def candle_atr(candles, index, period=14):
    start=max(1,index-period+1); trs=[]
    for j in range(start,index+1):
        c,prev=candles[j],candles[j-1]["close"]
        trs.append(max(c["high"]-c["low"],abs(c["high"]-prev),abs(c["low"]-prev)))
    return sum(trs)/len(trs) if trs else 0.0

def multi_timeframe_trend_alignment(data):
    """Compare confirmed EMA structure across available timeframes."""
    details = {}
    directions = []
    for tf, candles in data.items():
        closes = [x["close"] for x in candles]
        if len(closes) < 200:
            details[tf] = {"trend": "INSUFFICIENT HISTORY", "samples": len(closes)}
            continue
        e20 = ema(closes, 20)
        e50 = ema(closes, 50)
        e200 = ema(closes, 200)
        last = closes[-1]
        slope = (e20[-1] / e20[-6] - 1.0) if e20[-6] else 0.0
        if last > e20[-1] > e50[-1] > e200[-1] and slope > 0:
            trend, direction = "BULLISH", 1
        elif last < e20[-1] < e50[-1] < e200[-1] and slope < 0:
            trend, direction = "BEARISH", -1
        else:
            trend, direction = "MIXED", 0
        directions.append(direction)
        details[tf] = {
            "trend": trend,
            "ema20_slope_pct": round(slope * 100, 4),
            "close_vs_ema200_pct": round((last / e200[-1] - 1.0) * 100, 4) if e200[-1] else 0.0,
            "samples": len(closes)
        }
    total = len(directions)
    bullish = sum(1 for x in directions if x == 1)
    bearish = sum(1 for x in directions if x == -1)
    mixed = total - bullish - bearish
    score = (bullish - bearish) / total if total else 0.0
    if total < 3:
        label = "INSUFFICIENT TIMEFRAMES"
    elif bullish >= total - 1:
        label = "STRONG BULLISH ALIGNMENT"
    elif bearish >= total - 1:
        label = "STRONG BEARISH ALIGNMENT"
    elif bullish > bearish:
        label = "PARTIAL BULLISH ALIGNMENT"
    elif bearish > bullish:
        label = "PARTIAL BEARISH ALIGNMENT"
    else:
        label = "MIXED / NO ALIGNMENT"
    return {
        "alignment": label,
        "alignment_score": round(score, 3),
        "bullish_timeframes": bullish,
        "bearish_timeframes": bearish,
        "mixed_timeframes": mixed,
        "timeframes_checked": total,
        "by_timeframe": details,
        "note": "EMA alignment is contextual confirmation, not a standalone entry signal."
    }


def advanced_regime(data):
    c=data["5m"]; closes=[x["close"] for x in c]
    e20=ema(closes,20); e50=ema(closes,50); e200=ema(closes,200)
    atr_now=candle_atr(c,len(c)-1,14)
    av=[candle_atr(c,i,14) for i in range(max(20,len(c)-120),len(c))]
    atr_base=sum(av)/len(av) if av else atr_now
    slope=e20[-1]/e50[-1]-1.0 if e50[-1] else 0.0
    bull=closes[-1]>e50[-1]>e200[-1]
    bear=closes[-1]<e50[-1]<e200[-1]
    ratio=atr_now/atr_base if atr_base else 1.0
    if ratio>1.45: regime="HIGH VOLATILITY"
    elif bull and slope>0.001: regime="BULL TREND"
    elif bear and slope< -0.001: regime="BEAR TREND"
    elif abs(slope)<0.0015: regime="RANGE / CHOP"
    else: regime="TRANSITION"
    prior=c[-21:-1]; hi=max(x["high"] for x in prior); lo=min(x["low"] for x in prior); last=c[-1]
    sweep="NONE"
    if last["high"]>hi and last["close"]<hi: sweep="BEARISH HIGH-SIDE LIQUIDITY SWEEP"
    elif last["low"]<lo and last["close"]>lo: sweep="BULLISH LOW-SIDE LIQUIDITY SWEEP"
    return {"regime":regime,"atr_ratio":round(ratio,3),"trend_slope_pct":round(slope*100,4),
            "liquidity_sweep":sweep,"range_high":round(hi,2),"range_low":round(lo,2),
            "explanation":"Volatility expansion" if regime=="HIGH VOLATILITY" else
            "EMA structure and slope" if regime in ("BULL TREND","BEAR TREND") else
            "Low directional separation" if regime=="RANGE / CHOP" else "Trend transition"}

def advanced_horizon_models(data, prepared):
    base=data["5m"]; output={}
    for horizon,label in ((3,"15m"),(6,"30m"),(12,"60m"),(48,"4h")):
        rows,labels=[],[]; start=max(200,len(base)-1400)
        for i in range(start,len(base)-horizon):
            x=combined_features(data,i,prepared)
            if x is None: continue
            move=base[i+horizon]["close"]/base[i]["close"]-1.0
            vol=candle_atr(base,i,14)/base[i]["close"]
            threshold=max(0.00035,vol*0.20*math.sqrt(horizon/3.0))
            labels.append(2 if move>threshold else 0 if move< -threshold else 1); rows.append(x)
        if len(rows)<300:
            output[label]={"signal":"WAIT","status":"INSUFFICIENT DATA","samples":len(rows)}; continue
        split=int(len(rows)*0.80); purge=max(3,horizon)
        train_rows,train_labels=rows[:max(0,split-purge)],labels[:max(0,split-purge)]
        test_rows,test_labels=rows[split:],labels[split:]
        if len(train_rows)<200 or len(test_rows)<30:
            output[label]={"signal":"WAIT","status":"INSUFFICIENT VALIDATION","samples":len(rows)}; continue
        model=train_model(train_rows,train_labels,epochs=30,learning_rate=0.02,l2=0.015)
        probs_test=[predict(model,x) for x in test_rows]
        pred=[max(range(3),key=lambda k:p[k]) for p in probs_test]
        metrics=classification_metrics(test_labels,pred)
        latest=combined_features(data,len(base)-1,prepared); probs=predict(model,latest)
        ranked=sorted(probs,reverse=True); margin=ranked[0]-ranked[1]
        candidate=CLASSES[max(range(3),key=lambda k:probs[k])]
        signal=candidate if candidate!="WAIT" and metrics["accuracy"]>=38.0 and metrics["macro_f1"]>=25.0 and margin>=0.10 else "WAIT"
        output[label]={"signal":signal,"raw_model_class":candidate,
            "scores":{name:round(probs[k]*100,2) for k,name in enumerate(CLASSES)},
            "confidence_margin":round(margin*100,2),"holdout_accuracy":metrics["accuracy"],
            "macro_f1":metrics["macro_f1"],"training_samples":len(train_rows),
            "validation_samples":len(test_rows),
            "status":"VALIDATED HOLDOUT" if signal!="WAIT" else "WEAK EDGE — WAIT"}
    return output



def temporal_sequence_forecast(rows, labels, sequence_length=12):
    """Train a neural model on ordered windows of the previous 12 five-minute feature vectors."""
    try:
        import numpy as np
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        from sklearn.neural_network import MLPClassifier
    except Exception as exc:
        return {"signal": "WAIT", "status": "DEPENDENCY ERROR", "detail": str(exc)[:200]}
    if len(rows) != len(labels) or len(rows) < sequence_length + 350:
        return {"signal": "WAIT", "status": "INSUFFICIENT SEQUENCE HISTORY",
                "samples": len(rows), "sequence_length": sequence_length}
    sequences, sequence_labels = [], []
    for end in range(sequence_length - 1, len(rows)):
        window = rows[end - sequence_length + 1:end + 1]
        sequences.append([value for timestep in window for value in timestep])
        sequence_labels.append(labels[end])
    split = int(len(sequences) * 0.80)
    purge = sequence_length + FUTURE_CANDLES
    train_end = split - purge
    x_train = np.asarray(sequences[:train_end], dtype=float)
    y_train = np.asarray(sequence_labels[:train_end], dtype=int)
    x_test = np.asarray(sequences[split:], dtype=float)
    y_test = np.asarray(sequence_labels[split:], dtype=int)
    if len(x_train) < 250 or len(x_test) < 40 or len(set(y_train.tolist())) < 2:
        return {"signal": "WAIT", "status": "INSUFFICIENT PURGED HOLDOUT",
                "training_samples": len(x_train), "validation_samples": len(x_test)}
    network = make_pipeline(
        StandardScaler(),
        MLPClassifier(hidden_layer_sizes=(96, 48, 24), activation="relu",
                      solver="adam", alpha=0.02, batch_size=64,
                      learning_rate_init=0.0007, max_iter=100,
                      early_stopping=False, shuffle=False, random_state=42)
    )
    try:
        network.fit(x_train, y_train)
        raw = network.predict_proba(x_test)
        fitted_classes = list(network[-1].classes_)
        mapped = [[float(row[fitted_classes.index(k)]) if k in fitted_classes else 0.0
                   for k in range(len(CLASSES))] for row in raw]
        predictions = [max(range(3), key=lambda k: probs[k]) for probs in mapped]
        metrics = classification_metrics(y_test.tolist(), predictions)
        majority = max(sequence_labels[split:].count(k) / max(1, len(y_test)) * 100.0
                       for k in range(len(CLASSES)))
        latest_raw = network.predict_proba(np.asarray([sequences[-1]], dtype=float))[0]
        latest_probs = [float(latest_raw[fitted_classes.index(k)]) if k in fitted_classes else 0.0
                        for k in range(3)]
        total = sum(latest_probs) or 1.0
        latest_probs = [value / total for value in latest_probs]
        ranked = sorted(latest_probs, reverse=True)
        margin = ranked[0] - ranked[1]
        candidate = CLASSES[max(range(3), key=lambda k: latest_probs[k])]
        passed = (metrics["accuracy"] >= 38.0 and metrics["accuracy"] > majority
                  and metrics["macro_f1"] >= 30.0 and margin >= 0.10
                  and candidate != "WAIT")
        return {
            "signal": candidate if passed else "WAIT", "raw_model_class": candidate,
            "scores": {name: round(latest_probs[k] * 100, 2) for k, name in enumerate(CLASSES)},
            "confidence_margin": round(margin * 100, 2),
            "holdout_accuracy": metrics["accuracy"], "macro_f1": metrics["macro_f1"],
            "majority_baseline_pct": round(majority, 2),
            "training_samples": len(x_train), "validation_samples": len(x_test),
            "sequence_length": sequence_length,
            "input_order": "12 consecutive 5m feature vectors; oldest to newest",
            "architecture": "StandardScaler + MLP 96-48-24 (ReLU)",
            "status": "VALIDATED TEMPORAL MODEL" if passed else "WEAK EDGE — WAIT"
        }
    except Exception as exc:
        return {"signal": "WAIT", "status": "TRAINING ERROR", "detail": str(exc)[:240]}

def advanced_tree_ensemble(data, prepared):
    """Chronological Random Forest + gradient boosting + XGBoost ensemble."""
    try:
        import numpy as np
        from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        from sklearn.neural_network import MLPClassifier
    except Exception as exc:
        return {"status":"DEPENDENCY ERROR","detail":str(exc),"forecasts":{}}
    try:
        from xgboost import XGBClassifier
        xgb_available=True
    except Exception:
        XGBClassifier=None
        xgb_available=False
    base=data["5m"]; output={}
    for horizon,label in ((3,"15m"),(6,"30m"),(12,"60m"),(48,"4h")):
        rows,labels=[],[]; start=max(200,len(base)-1500)
        for i in range(start,len(base)-horizon):
            x=combined_features(data,i,prepared)
            if x is None: continue
            move=base[i+horizon]["close"]/base[i]["close"]-1.0
            vol=candle_atr(base,i,14)/base[i]["close"]
            threshold=max(0.00035,vol*0.20*math.sqrt(horizon/3.0))
            rows.append(x); labels.append(2 if move>threshold else 0 if move< -threshold else 1)
        if len(rows)<300:
            output[label]={"signal":"WAIT","status":"INSUFFICIENT DATA","samples":len(rows)}
            continue
        split=int(len(rows)*0.80); purge=max(3,horizon)
        x_train=np.asarray(rows[:split-purge],dtype=float)
        y_train=np.asarray(labels[:split-purge],dtype=int)
        x_test=np.asarray(rows[split:],dtype=float)
        y_test=np.asarray(labels[split:],dtype=int)
        if len(x_train)<200 or len(x_test)<30:
            output[label]={"signal":"WAIT","status":"INSUFFICIENT VALIDATION","samples":len(rows)}
            continue
        models={
            "random_forest":RandomForestClassifier(n_estimators=60,max_depth=8,min_samples_leaf=12,
                class_weight="balanced_subsample",n_jobs=2,random_state=42),
            "gradient_boosting":HistGradientBoostingClassifier(max_iter=100,learning_rate=0.06,
                max_leaf_nodes=15,l2_regularization=1.0,random_state=42),
            "neural_mlp":make_pipeline(StandardScaler(),MLPClassifier(hidden_layer_sizes=(48,24),
                activation="relu",solver="adam",alpha=0.01,batch_size=64,learning_rate_init=0.001,
                max_iter=60,early_stopping=True,validation_fraction=0.15,n_iter_no_change=8,
                random_state=42))
        }
        if xgb_available:
            models["xgboost"]=XGBClassifier(n_estimators=60,max_depth=3,learning_rate=0.04,
                subsample=0.85,colsample_bytree=0.85,min_child_weight=8,
                objective="multi:softprob",num_class=3,eval_metric="mlogloss",
                n_jobs=2,random_state=42,verbosity=0)
        all_probs=[]; model_metrics={}
        for name,estimator in models.items():
            try:
                estimator.fit(x_train,y_train)
                raw=estimator.predict_proba(x_test)
                classes=list(estimator.classes_)
                mapped=[[float(row[classes.index(k)]) if k in classes else 0.0 for k in range(3)] for row in raw]
                model_pred=[max(range(3),key=lambda k:p[k]) for p in mapped]
                metric=classification_metrics(y_test.tolist(),model_pred)
                latest_x=np.asarray([combined_features(data,len(base)-1,prepared)],dtype=float)
                latest_raw=estimator.predict_proba(latest_x)[0]
                latest_probs=[float(latest_raw[classes.index(k)]) if k in classes else 0.0 for k in range(3)]
                total=sum(latest_probs) or 1.0
                latest_probs=[p/total for p in latest_probs]
                all_probs.append(latest_probs)
                model_metrics[name]={"holdout_accuracy":metric["accuracy"],"macro_f1":metric["macro_f1"]}
            except Exception as exc:
                model_metrics[name]={"error":str(exc)[:180]}
        if not all_probs:
            output[label]={"signal":"WAIT","status":"ALL TREE MODELS FAILED","models":model_metrics}
            continue
        probs=[sum(p[k] for p in all_probs)/len(all_probs) for k in range(3)]
        ranked=sorted(probs,reverse=True); margin=ranked[0]-ranked[1]
        candidate=CLASSES[max(range(3),key=lambda k:probs[k])]
        avg_accuracy=sum(v["holdout_accuracy"] for v in model_metrics.values() if "holdout_accuracy" in v)/max(1,sum(1 for v in model_metrics.values() if "holdout_accuracy" in v))
        avg_f1=sum(v["macro_f1"] for v in model_metrics.values() if "macro_f1" in v)/max(1,sum(1 for v in model_metrics.values() if "macro_f1" in v))
        signal=candidate if candidate!="WAIT" and avg_accuracy>=38.0 and avg_f1>=25.0 and margin>=0.08 else "WAIT"
        output[label]={"signal":signal,"raw_model_class":candidate,
            "scores":{name:round(probs[k]*100,2) for k,name in enumerate(CLASSES)},
            "confidence_margin":round(margin*100,2),"holdout_accuracy":round(avg_accuracy,2),
            "macro_f1":round(avg_f1,2),"training_samples":len(x_train),"validation_samples":len(x_test),
            "models":model_metrics,"model_count":len(all_probs),
            "status":"VALIDATED TREE ENSEMBLE" if signal!="WAIT" else "WEAK EDGE — WAIT"}
    return {"status":"READY" if output else "NO FORECASTS",
            "algorithms":["Random Forest","Histogram Gradient Boosting","Neural MLP"]+(["XGBoost"] if xgb_available else []),
            "forecasts":output,
            "validation":"chronological holdout with horizon purge; scores are not calibrated win probabilities"}




def update_prediction_feedback(data, result, path="prediction_feedback.json"):
    """
    Resolve matured 15m forecasts against later closed candles and retain a
    bounded audit trail. This is evaluation feedback, not automatic retraining.
    """
    candles = data["5m"]
    candle_times = [item["time"] for item in candles]
    latest_time = candle_times[-1]
    try:
        with open(path, "r", encoding="utf-8") as handle:
            ledger = json.load(handle)
        if not isinstance(ledger, dict) or not isinstance(ledger.get("predictions"), list):
            ledger = {"version": 1, "predictions": []}
    except (OSError, ValueError, TypeError):
        ledger = {"version": 1, "predictions": []}

    records = ledger.get("predictions", [])
    for record in records:
        if record.get("status") != "PENDING":
            continue
        target_time = int(record.get("target_time", 0))
        target_index = bisect.bisect_left(candle_times, target_time)
        if target_index >= len(candles):
            continue
        # Do not score a forecast until the target candle itself is closed.
        actual_price = candles[target_index]["close"]
        start_price = float(record.get("reference_price", 0))
        if start_price <= 0:
            record["status"] = "INVALID"
            continue
        realized_return = actual_price / start_price - 1.0
        threshold = float(record.get("threshold_pct", MOVE_THRESHOLD))
        actual_class = 2 if realized_return > threshold else 0 if realized_return < -threshold else 1
        record.update({
            "status": "EVALUATED",
            "actual_class": CLASSES[actual_class],
            "actual_return_pct": round(realized_return * 100.0, 5),
            "actual_close": actual_price,
            "evaluated_at_candle_time": candle_times[target_index],
            "correct": record.get("predicted_class") == CLASSES[actual_class]
        })

    prediction_time = latest_time
    origin_index = len(candles) - 1
    target_time = prediction_time + FUTURE_CANDLES * INTERVALS["5m"] * 60_000
    threshold_pct = adaptive_move_threshold(candles, origin_index, FUTURE_CANDLES)
    models = {}
    scores = result.get("class_scores", {})
    if scores:
        models["primary_ensemble"] = {
            "class": max(CLASSES, key=lambda name: float(scores.get(name, 0.0))),
            "scores": scores,
            "approved_signal": result.get("signal", "WAIT")
        }
    intelligence = result.get("advanced_intelligence", {})
    temporal = intelligence.get("temporal_sequence_model", {})
    if temporal.get("raw_model_class") in CLASSES:
        models["temporal_sequence"] = {
            "class": temporal["raw_model_class"],
            "scores": temporal.get("scores", {}),
            "approved_signal": temporal.get("signal", "WAIT")
        }
    tree = intelligence.get("tree_ensemble", {}).get("forecasts", {}).get("15m", {})
    if tree.get("raw_model_class") in CLASSES:
        models["tree_ensemble_15m"] = {
            "class": tree["raw_model_class"],
            "scores": tree.get("scores", {}),
            "approved_signal": tree.get("signal", "WAIT")
        }

    known = {item.get("id") for item in records}
    for model_name, prediction in models.items():
        record_id = f"{model_name}:{prediction_time}:{target_time}"
        if record_id in known:
            continue
        records.append({
            "id": record_id,
            "model": model_name,
            "prediction_time": prediction_time,
            "target_time": target_time,
            "horizon_minutes": 15,
            "reference_price": candles[-1]["close"],
            "threshold_pct": threshold_pct,
            "predicted_class": prediction["class"],
            "approved_signal": prediction["approved_signal"],
            "scores": prediction["scores"],
            "status": "PENDING"
        })

    records = sorted(records, key=lambda item: int(item.get("prediction_time", 0)))[-1000:]
    summary = {}
    for model_name in ("primary_ensemble", "temporal_sequence", "tree_ensemble_15m"):
        evaluated = [item for item in records
                     if item.get("model") == model_name and item.get("status") == "EVALUATED"]
        pending = sum(1 for item in records
                      if item.get("model") == model_name and item.get("status") == "PENDING")
        if evaluated:
            accuracy = sum(1 for item in evaluated if item.get("correct")) / len(evaluated) * 100.0
            class_f1 = []
            for class_name in CLASSES:
                tp = sum(1 for item in evaluated
                         if item.get("predicted_class") == class_name
                         and item.get("actual_class") == class_name)
                fp = sum(1 for item in evaluated
                         if item.get("predicted_class") == class_name
                         and item.get("actual_class") != class_name)
                fn_count = sum(1 for item in evaluated
                               if item.get("predicted_class") != class_name
                               and item.get("actual_class") == class_name)
                precision = tp / (tp + fp) if tp + fp else 0.0
                recall = tp / (tp + fn_count) if tp + fn_count else 0.0
                class_f1.append(2 * precision * recall / (precision + recall)
                                if precision + recall else 0.0)
            macro_f1 = sum(class_f1) / len(CLASSES) * 100.0
        else:
            accuracy, macro_f1 = None, None
        summary[model_name] = {
            "evaluated_predictions": len(evaluated),
            "pending_predictions": pending,
            "realized_accuracy_pct": round(accuracy, 2) if accuracy is not None else None,
            "realized_macro_f1_pct": round(macro_f1, 2) if macro_f1 is not None else None,
            "status": "COLLECTING LIVE OUTCOMES" if len(evaluated) < 30 else "LIVE FEEDBACK SAMPLE"
        }

    ledger = {
        "version": 1,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "scoring_rule": "15m realized return classified using the prediction-time ATR neutral threshold",
        "predictions": records
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(ledger, handle, indent=2)
    return {
        "status": "ACTIVE",
        "method": "matured forecast vs subsequent closed 5m candle",
        "history_retained": len(records),
        "models": summary,
        "note": "Live outcomes are tracked for evaluation; this does not silently retrain or alter model weights."
    }

def main():
    print("BTC Signal Pro Elite - Advanced ML starting...")
    data = {}
    source = "Binance USD-M Futures public klines"
    symbol = "BTC/USDT"
    data_source_by_tf = {}
    for tf in INTERVALS:
        print(f"Loading latest Binance USD-M Futures {tf} candles...")
        try:
            data[tf] = get_binance_data(tf)
            data_source_by_tf[tf] = "Binance USD-M Futures"
        except Exception as futures_error:
            print(f"Binance Futures unavailable for {tf}: {futures_error}")
            interval_minutes = INTERVALS[tf]
            print(f"Trying Kraken public BTC/USD {interval_minutes}m candles...")
            try:
                data[tf] = get_kraken_data(interval_minutes)
                data_source_by_tf[tf] = "Kraken BTC/USD"
            except Exception as fallback_error:
                raise RuntimeError(
                    f"No fresh market data for {tf}. Binance Futures: {futures_error}; "
                    f"Kraken fallback: {fallback_error}"
                ) from fallback_error
    used_sources = sorted(set(data_source_by_tf.values()))
    source = " + ".join(used_sources) + " public OHLC candles"
    if len(used_sources) > 1:
        print(f"Mixed market data sources by timeframe: {data_source_by_tf}")
    else:
        print(f"Market data source: {source}")
    latest_candle_time = data["5m"][-1]["time"]
    data_age_minutes = round((datetime.now(timezone.utc).timestamp() * 1000 - latest_candle_time) / 60000, 2)
    if data_age_minutes > 15:
        raise ValueError(f"STALE DATA BLOCK: 5m model input is {data_age_minutes} minutes old")
    prepared = {tf: precompute_candle_features(candles) for tf, candles in data.items()}
    rows, labels = build_dataset(data, prepared)
    if len(rows) < 250:
        raise ValueError(f"Not enough aligned multi-timeframe samples: {len(rows)}")
    validation, folds = walk_forward_validate(rows, labels)
    final_wait_threshold = tune_wait_threshold(rows, labels)
    final_model = train_ensemble(rows, labels)
    class_counts = [max(1, labels.count(k)) for k in range(len(CLASSES))]
    class_weights = [min(2.5, len(labels) / (len(CLASSES) * count)) for count in class_counts]
    latest = combined_features(data, len(data["5m"]) - 1, prepared)
    if latest is None:
        raise ValueError("Could not build latest multi-timeframe features.")
    scores = predict_ensemble(final_model, latest)
    regime_analysis = advanced_regime(data)
    horizon_forecasts = advanced_horizon_models(data, prepared)
    temporal_model = temporal_sequence_forecast(rows, labels, sequence_length=12)
    tree_ensemble = advanced_tree_ensemble(data, prepared)
    tree_blend_applied = False
    tree_primary = tree_ensemble.get("forecasts", {}).get("15m", {})
    if tree_primary.get("signal") in ("LONG", "SHORT") and tree_primary.get("macro_f1", 0) >= 25.0:
        tree_scores = [float(tree_primary.get("scores", {}).get(name, 0)) / 100.0 for name in CLASSES]
        scores = [0.65 * scores[k] + 0.35 * tree_scores[k] for k in range(3)]
        tree_blend_applied = True
    best = max(range(3), key=lambda k: scores[k])
    ordered = sorted(scores, reverse=True)
    margin = ordered[0] - ordered[1]
    calibrated_class = predict_with_wait_threshold(scores, final_wait_threshold)
    validation_gate = (
        validation.get("accuracy", 0) >= 38.0
        and validation.get("macro_f1", 0) >= 30.0
        and validation.get("accuracy", 0) > validation.get("majority_class_baseline_pct", 100)
    )
    signal = (
        CLASSES[calibrated_class]
        if validation_gate and calibrated_class == 1
        else CLASSES[calibrated_class]
        if validation_gate and scores[calibrated_class] >= 0.48 and margin >= 0.10
        else "WAIT"
    )
    trend_alignment = multi_timeframe_trend_alignment(data)
    alignment_veto = None
    if signal == "LONG" and trend_alignment["bearish_timeframes"] >= 3 and trend_alignment["bearish_timeframes"] > trend_alignment["bullish_timeframes"]:
        signal = "WAIT"
        alignment_veto = "LONG blocked: at least 3 timeframes have bearish EMA structure."
    elif signal == "SHORT" and trend_alignment["bullish_timeframes"] >= 3 and trend_alignment["bullish_timeframes"] > trend_alignment["bearish_timeframes"]:
        signal = "WAIT"
        alignment_veto = "SHORT blocked: at least 3 timeframes have bullish EMA structure."
    temporal_veto = None
    if signal in ("LONG", "SHORT") and temporal_model.get("signal") in ("LONG", "SHORT") and temporal_model["signal"] != signal:
        signal = "WAIT"
        temporal_veto = "Primary signal conflicts with the independently validated temporal neural model."
    valid_horizon_signals = {
        timeframe: forecast.get("signal")
        for timeframe, forecast in horizon_forecasts.items()
        if isinstance(forecast, dict) and forecast.get("signal") in ("LONG", "SHORT")
    }
    opposite_horizon_signals = [
        timeframe for timeframe, forecast_signal in valid_horizon_signals.items()
        if signal in ("LONG", "SHORT") and forecast_signal != signal
    ]
    horizon_consensus_veto = None
    if signal in ("LONG", "SHORT") and len(opposite_horizon_signals) >= 2:
        signal = "WAIT"
        horizon_consensus_veto = (
            "Directional signal blocked: at least two independently validated "
            "forecast horizons disagree."
        )
    horizon_consensus = {
        "validated_directional_forecasts": valid_horizon_signals,
        "opposite_horizons_to_primary": opposite_horizon_signals,
        "opposite_horizon_count": len(opposite_horizon_signals),
        "veto_threshold": 2,
        "veto_reason": horizon_consensus_veto,
        "note": "Only horizons passing their own validation gate are counted; this is a conflict filter, not a guarantee."
    }
    model_probabilities = {
        "full_history_logistic": predict(final_model["full_history"], latest),
        "recent_regime_logistic": predict(final_model["recent_regime"], latest),
        "nearest_centroid": predict_centroid(final_model["centroid"], latest),
        "decision_stumps": predict_stumps(final_model["stumps"], latest)
    }
    model_votes = {name: CLASSES[max(range(3), key=lambda k: probs[k])] for name, probs in model_probabilities.items()}
    result = {
        "system": "BTC Signal Pro Elite Advanced ML",
        "model": "Four-model vote: full-history logistic, recent-regime logistic, nearest-centroid and decision-stump ensemble",
        "model_version": "4.1-volatility-adaptive-labels",
        "data_source": source,
        "data_source_by_timeframe": data_source_by_tf,
        "symbol": "BTC/USD" if used_sources == ["Kraken BTC/USD"] else symbol,
        "quote_asset_note": ("Kraken BTC/USD spot is a public-data proxy, not identical to Binance BTC/USDT Futures." if "Kraken BTC/USD" in used_sources else "Binance USD-M BTC/USDT Futures market data."),
        "base_interval": "5m",
        "feature_timeframes": list(INTERVALS.keys()),
        "signal": signal,
        "class_scores": {name: round(scores[i] * 100, 2) for i, name in enumerate(CLASSES)},
        "individual_model_votes": model_votes,
        "individual_model_scores": {name: {cls: round(probs[i] * 100, 2) for i, cls in enumerate(CLASSES)} for name, probs in model_probabilities.items()},
        "confidence_score": round(scores[best] * 100, 2),
        "confidence_margin": round(margin * 100, 2),
        "wait_probability_threshold": final_wait_threshold,
        "ensemble_configuration": {"models": ["full_history_logistic", "recent_regime_logistic", "nearest_centroid", "decision_stumps"], "weights": {"full_history_logistic": 0.35, "recent_regime_logistic": 0.20, "nearest_centroid": 0.20, "decision_stumps": 0.25}, "recent_training_window": final_model["recent_window"]},
        "walk_forward_metrics": validation,
        "validation_gate": {"passed": validation_gate, "minimum_accuracy_pct": 38.0, "minimum_macro_f1_pct": 30.0, "must_beat_majority_baseline": True, "decision": "directional signals allowed" if validation_gate else "WAIT only — historical validation too weak"},
        "advanced_intelligence": {"market_regime": regime_analysis, "multi_timeframe_trend_alignment": trend_alignment, "alignment_veto": alignment_veto, "multi_horizon_forecasts": horizon_forecasts, "multi_horizon_consensus": horizon_consensus, "temporal_sequence_model": temporal_model, "temporal_model_veto": temporal_veto, "tree_ensemble": tree_ensemble, "tree_blend_applied_to_primary_signal": tree_blend_applied, "method": "ordered 12-candle temporal neural network + four-horizon chronological ML + Random Forest + gradient boosting + neural MLP + XGBoost (when installed) + volatility regime + liquidity sweep detector"},
        "walk_forward_folds": folds,
        "training_samples": len(rows),
        "label_distribution": {name: labels.count(i) for i, name in enumerate(CLASSES)},
        "training_class_weights": {name: weight for name, weight in zip(CLASSES, class_weights)},
        "recency_weighting": {"method": "exponential", "half_life_training_samples": 1800},
        "future_horizon": f"{FUTURE_CANDLES} candles / 15 minutes",
        "neutral_threshold_pct": MOVE_THRESHOLD * 100,
        "labeling_method": "volatility-adaptive ATR neutral band",
        "label_threshold": {
            "formula": "max(0.035%, 20% of trailing 14-candle ATR as a fraction of price)",
            "horizon_candles": FUTURE_CANDLES,
            "lookahead_free": True
        },
        "last_price": data["5m"][-1]["close"],
        "last_candle_time": data["5m"][-1]["time"],
        "data_freshness": {
            "status": "FRESH",
            "latest_5m_candle_age_minutes": data_age_minutes,
            "latest_5m_candle_time": datetime.fromtimestamp(latest_candle_time / 1000, timezone.utc).isoformat(),
            "source_by_timeframe": data_source_by_tf,
            "max_allowed_age_minutes": 15
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "limitations": [
            "When Binance USD-M Futures is unavailable, Kraken BTC/USD spot OHLC is used as a proxy and is not equivalent to BTC/USDT Futures. Funding, open interest, CVD, order book and liquidation remain separate confluence inputs.",
            "Class scores are model scores, not calibrated real-world win probabilities.",
            "Walk-forward metrics are historical diagnostics, not guarantees of future performance."
        ]
    }
    result["prediction_feedback"] = update_prediction_feedback(data, result)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))
    print(f"Saved result to {OUTPUT_FILE}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("AI MODEL ERROR:", str(exc))
        raise