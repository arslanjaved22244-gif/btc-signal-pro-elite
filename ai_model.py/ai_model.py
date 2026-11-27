#!/usr/bin/env python3
"""
BTC Signal Pro Elite - Advanced Multi-Timeframe ML Engine.
Public Kraken Spot OHLCV only; no API keys and no orders are placed.
"""
import bisect
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
    # Public Binance USD-M Futures endpoint; no API key is required.
    # Page backward to build a larger history than Kraken's 720-candle limit.
    candles_by_time = {}
    end_time = None
    for _ in range(3):
        query = f"symbol=BTCUSDT&interval={interval}&limit=1000"
        if end_time is not None:
            query += f"&endTime={end_time}"
        url = "https://fapi.binance.com/fapi/v1/klines?" + query
        request = urllib.request.Request(
            url, headers={"User-Agent": "BTC-Signal-Pro-Elite/3.0"}
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
        raise ValueError(f"Not enough completed Binance {interval} candles: {len(candles)}")
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


def combined_features(data, base_index):
    base = data["5m"]
    timestamp = base[base_index]["time"]
    features = candle_features(base, base_index)
    if features is None:
        return None
    for tf in ("15m", "1h", "4h", "1d"):
        candles = data[tf]
        idx = latest_closed_index(candles, timestamp, INTERVALS[tf])
        if idx < 199:
            return None
        values = candle_features(candles, idx)
        if values is None:
            return None
        features.extend([
            values[0], values[2], values[5], values[6],
            values[7], values[8], values[9], values[10]
        ])
    return features


def build_dataset(data):
    candles = data["5m"]
    rows, labels = [], []
    for i in range(200, len(candles) - FUTURE_CANDLES):
        features = combined_features(data, i)
        if features is None:
            continue
        move = candles[i + FUTURE_CANDLES]["close"] / candles[i]["close"] - 1.0
        label = 2 if move > MOVE_THRESHOLD else 0 if move < -MOVE_THRESHOLD else 1
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
    class_counts = [max(1, labels.count(k)) for k in range(len(CLASSES))]
    class_weights = [min(2.5, n / (len(CLASSES) * count)) for count in class_counts]
    weights = [[0.0] * p for _ in CLASSES]
    biases = [0.0] * len(CLASSES)
    for _ in range(epochs):
        grad_w = [[0.0] * p for _ in CLASSES]
        grad_b = [0.0] * len(CLASSES)
        for row, actual in zip(x, labels):
            probs = softmax([
                biases[k] + sum(weights[k][j] * row[j] for j in range(p))
                for k in range(len(CLASSES))
            ])
            for k in range(len(CLASSES)):
                error = (probs[k] - (1.0 if k == actual else 0.0)) * class_weights[actual]
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


def walk_forward_validate(rows, labels):
    n = len(rows)
    test_size = n // 6
    if test_size < 25:
        raise ValueError("Not enough samples for three walk-forward test windows.")
    actual_all, predicted_all, fold_results = [], [], []
    for fold in range(3):
        test_start = n // 2 + fold * test_size
        test_end = min(n, test_start + test_size)
        train_end = test_start - FUTURE_CANDLES  # purge overlapping future labels
        if train_end < 150 or test_end <= test_start:
            continue
        model = train_model(rows[:train_end], labels[:train_end])
        fold_actual, fold_predicted = [], []
        for row, actual in zip(rows[test_start:test_end], labels[test_start:test_end]):
            probs = predict(model, row)
            fold_actual.append(actual)
            fold_predicted.append(max(range(3), key=lambda k: probs[k]))
        fold_metrics = classification_metrics(fold_actual, fold_predicted)
        fold_results.append({
            "fold": fold + 1, "train_samples": train_end,
            "test_samples": len(fold_actual), "metrics": fold_metrics
        })
        actual_all.extend(fold_actual)
        predicted_all.extend(fold_predicted)
    if not actual_all:
        raise ValueError("Walk-forward validation could not create a valid fold.")
    return classification_metrics(actual_all, predicted_all), fold_results


def main():
    print("BTC Signal Pro Elite - Advanced ML starting...")
    data = {}
    source = "Binance USD-M Futures public klines"
    symbol = "BTC/USDT"
    try:
        for tf in INTERVALS:
            print(f"Loading Binance Futures {tf} candles...")
            data[tf] = get_binance_data(tf)
    except Exception as futures_exc:
        print(f"Binance Futures unavailable ({futures_exc}); trying Binance Spot.")
        data = {}
        try:
            for tf in INTERVALS:
                print(f"Loading Binance Spot {tf} candles...")
                data[tf] = get_binance_spot_data(tf)
            source = "Binance Spot public klines (fallback)"
            symbol = "BTC/USDT"
        except Exception as spot_exc:
            print(f"Binance Spot unavailable ({spot_exc}); trying Coinbase Exchange.")
            data = {}
            try:
                print("Loading Coinbase Exchange 5m history...")
                data["5m"] = get_coinbase_data(300, 3000)
                print("Loading Coinbase Exchange 15m history...")
                data["15m"] = get_coinbase_data(900, 3000)
                print("Loading Coinbase Exchange 1h history for 1h and 4h...")
                hourly = get_coinbase_data(3600, 3000)
                data["1h"] = hourly
                data["4h"] = aggregate_four_hour(hourly)
                print("Loading Coinbase Exchange 1d history...")
                data["1d"] = get_coinbase_data(86400, 400)
                source = "Coinbase Exchange public candles (fallback)"
                symbol = "BTC/USD"
            except Exception as coinbase_exc:
                print(f"Coinbase unavailable ({coinbase_exc}); switching all timeframes to Kraken Spot.")
                data = {}
                source = "Kraken Spot public OHLC (fallback)"
                symbol = "BTC/USD"
                for tf, minutes in INTERVALS.items():
                    print(f"Loading Kraken Spot {tf} candles...")
                    data[tf] = get_kraken_data(minutes)
    rows, labels = build_dataset(data)
    if len(rows) < 250:
        raise ValueError(f"Not enough aligned multi-timeframe samples: {len(rows)}")
    validation, folds = walk_forward_validate(rows, labels)
    final_model = train_model(rows, labels)
    latest = combined_features(data, len(data["5m"]) - 1)
    if latest is None:
        raise ValueError("Could not build latest multi-timeframe features.")
    scores = predict(final_model, latest)
    best = max(range(3), key=lambda k: scores[k])
    ordered = sorted(scores, reverse=True)
    margin = ordered[0] - ordered[1]
    signal = CLASSES[best] if best == 1 or (scores[best] >= 0.48 and margin >= 0.10) else "WAIT"
    result = {
        "system": "BTC Signal Pro Elite Advanced ML",
        "model": "Multinomial Logistic Regression",
        "model_version": "3.2-expanded-history-multi-source-walkforward",
        "data_source": source,
        "symbol": symbol,
        "base_interval": "5m",
        "feature_timeframes": list(INTERVALS.keys()),
        "signal": signal,
        "class_scores": {name: round(scores[i] * 100, 2) for i, name in enumerate(CLASSES)},
        "confidence_score": round(scores[best] * 100, 2),
        "confidence_margin": round(margin * 100, 2),
        "walk_forward_metrics": validation,
        "walk_forward_folds": folds,
        "training_samples": len(rows),
        "future_horizon": f"{FUTURE_CANDLES} candles / 15 minutes",
        "neutral_threshold_pct": MOVE_THRESHOLD * 100,
        "last_price": data["5m"][-1]["close"],
        "last_candle_time": data["5m"][-1]["time"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "limitations": [
            "Uses one consistent OHLCV source across all timeframes. Source fallback order is Binance USD-M Futures, Binance Spot, Coinbase Exchange, then Kraken Spot. Coinbase 4h candles are aggregated from 1h candles. Spot sources are not Futures data; CVD, open interest, funding, order book and liquidation feeds are not model features yet.",
            "Class scores are model scores, not calibrated real-world win probabilities.",
            "Walk-forward metrics are historical diagnostics, not guarantees of future performance."
        ]
    }
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
