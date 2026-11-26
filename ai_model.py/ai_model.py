
#!/usr/bin/env python3
"""
BTC Signal Pro Elite - Free Machine Learning Engine
Public Binance Futures data. No API key required.
Manual trading signals only. No orders are placed.
"""

import json
import math
import time
import urllib.request
from datetime import datetime, timezone

SYMBOL = "BTCUSDT"
INTERVAL = "5m"
LIMIT = 1000
FUTURE_CANDLES = 3
MOVE_THRESHOLD = 0.0005
OUTPUT_FILE = "ai_result.json"


def get_binance_data():
    url = (
        "https://fapi.binance.com/fapi/v1/klines"
        f"?symbol={SYMBOL}&interval={INTERVAL}&limit={LIMIT}"
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "BTC-Signal-Pro-Elite/1.0"}
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        raw = json.loads(response.read().decode("utf-8"))

    candles = []
    for row in raw:
        candles.append({
            "time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5])
        })

    # Exclude the currently forming candle.
    return candles[:-1]


def ema(values, period):
    if not values:
        return []

    result = [values[0]]
    alpha = 2.0 / (period + 1.0)

    for value in values[1:]:
        result.append(alpha * value + (1.0 - alpha) * result[-1])

    return result


def rsi(values, period=14):
    if len(values) <= period:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def make_features(candles, index):
    closes = [c["close"] for c in candles[:index + 1]]
    volumes = [c["volume"] for c in candles[:index + 1]]

    current = candles[index]
    price = current["close"]

    if price <= 0:
        return None

    def return_pct(period):
        old = closes[-1 - period]
        return (price / old) - 1.0 if old else 0.0

    ema9 = ema(closes, 9)[-1]
    ema21 = ema(closes, 21)[-1]
    ema50 = ema(closes, 50)[-1]

    avg_volume = sum(volumes[-20:]) / min(20, len(volumes))
    volume_ratio = (
        current["volume"] / avg_volume if avg_volume > 0 else 1.0
    )

    candle_range = (
        (current["high"] - current["low"]) / price
    )
    candle_body = (
        (current["close"] - current["open"]) / price
    )

    return [
        return_pct(1),
        return_pct(3),
        return_pct(6),
        return_pct(12),
        return_pct(24),
        rsi(closes) / 100.0,
        (ema9 / ema21) - 1.0 if ema21 else 0.0,
        (ema21 / ema50) - 1.0 if ema50 else 0.0,
        min(volume_ratio, 5.0),
        candle_range,
        candle_body
    ]


def build_dataset(candles):
    rows = []
    labels = []

    for i in range(50, len(candles) - FUTURE_CANDLES):
        features = make_features(candles, i)

        if features is None:
            continue

        future_price = candles[i + FUTURE_CANDLES]["close"]
        current_price = candles[i]["close"]
        future_return = (future_price / current_price) - 1.0

        # 1 = upward movement, 0 = downward/neutral movement.
        label = 1 if future_return > MOVE_THRESHOLD else 0

        rows.append(features)
        labels.append(label)

    return rows, labels


def sigmoid(value):
    value = max(-30.0, min(30.0, value))
    return 1.0 / (1.0 + math.exp(-value))


def standardize_fit(rows):
    columns = len(rows[0])
    means = []
    scales = []

    for j in range(columns):
        values = [row[j] for row in rows]
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        scale = math.sqrt(variance) or 1.0

        means.append(mean)
        scales.append(scale)

    return means, scales


def standardize(rows, means, scales):
    return [
        [(row[j] - means[j]) / scales[j] for j in range(len(row))]
        for row in rows
    ]


def train_model(rows, labels, epochs=450, learning_rate=0.04):
    means, scales = standardize_fit(rows)
    data = standardize(rows, means, scales)

    weights = [0.0] * len(data[0])
    bias = 0.0
    count = len(data)

    for epoch in range(epochs):
        gradients = [0.0] * len(weights)
        bias_gradient = 0.0

        for row, label in zip(data, labels):
            score = bias + sum(
                weights[j] * row[j] for j in range(len(weights))
            )
            prediction = sigmoid(score)
            error = prediction - label

            for j in range(len(weights)):
                gradients[j] += error * row[j]

            bias_gradient += error

        for j in range(len(weights)):
            gradients[j] = gradients[j] / count + 0.001 * weights[j]
            weights[j] -= learning_rate * gradients[j]

        bias -= learning_rate * bias_gradient / count

    return {
        "weights": weights,
        "bias": bias,
        "means": means,
        "scales": scales
    }


def predict_probability(model, row):
    normalized = [
        (row[j] - model["means"][j]) / model["scales"][j]
        for j in range(len(row))
    ]

    score = model["bias"] + sum(
        model["weights"][j] * normalized[j]
        for j in range(len(normalized))
    )

    return sigmoid(score)


def evaluate_model(rows, labels):
    split = int(len(rows) * 0.80)

    train_rows = rows[:split]
    train_labels = labels[:split]
    test_rows = rows[split:]
    test_labels = labels[split:]

    if len(train_rows) < 100 or len(test_rows) < 20:
        raise ValueError("Not enough historical candles for reliable testing.")

    test_model = train_model(train_rows, train_labels)

    correct = 0
    for row, actual in zip(test_rows, test_labels):
        probability = predict_probability(test_model, row)
        predicted = 1 if probability >= 0.5 else 0
        if predicted == actual:
            correct += 1

    accuracy = correct / len(test_rows)

    # Refit using all available historical examples for latest prediction.
    final_model = train_model(rows, labels)

    return final_model, accuracy, len(train_rows), len(test_rows)


def main():
    print("BTC Signal Pro Elite - Free ML starting...")
    candles = get_binance_data()

    if len(candles) < 200:
        raise ValueError("Not enough Binance candle data.")

    rows, labels = build_dataset(candles)

    if len(rows) < 150:
        raise ValueError("Not enough training examples.")

    model, test_accuracy, train_count, test_count = evaluate_model(
        rows, labels
    )

    latest_features = make_features(candles, len(candles) - 1)
    probability_up = predict_probability(model, latest_features)
    probability_down = 1.0 - probability_up

    difference = abs(probability_up - probability_down)

    if difference < 0.10 or max(probability_up, probability_down) < 0.55:
        signal = "WAIT"
    elif probability_up > probability_down:
        signal = "LONG"
    else:
        signal = "SHORT"

    result = {
        "system": "BTC Signal Pro Elite Free ML",
        "model": "Logistic Regression",
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "signal": signal,
        "probability_up": round(probability_up * 100, 2),
        "probability_down": round(probability_down * 100, 2),
        "test_accuracy": round(test_accuracy * 100, 2),
        "training_samples": train_count,
        "testing_samples": test_count,
        "historical_examples": len(rows),
        "last_price": candles[-1]["close"],
        "last_candle_time": candles[-1]["time"],
        "future_horizon": f"{FUTURE_CANDLES} candles / 15 minutes",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "warning": (
            "Experimental ML output, not guaranteed probability or "
            "financial advice. Backtest results do not guarantee future results."
        )
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(result, file, indent=2)

    print(json.dumps(result, indent=2))
    print(f"\nSaved result to {OUTPUT_FILE}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("AI MODEL ERROR:", str(error))
        raise
