# BTC Signal Pro Elite

Phone-friendly BTC/USDT signal dashboard. It is **signal-only**: it does not connect to your Binance account, place orders, or require exchange API keys.

## Open the dashboard
GitHub Pages: https://arslanjaved22244-gif.github.io/btc-signal-pro-elite/

## Install on iPhone (free)
1. Open the GitHub Pages link above in **Safari** on your iPhone.
2. Tap the **Share** button (square with an up arrow).
3. Choose **Add to Home Screen**.
4. Keep the suggested name **BTC Signal** and tap **Add**.
5. Open the new BTC Signal icon from your Home Screen.

This is an installable web app (PWA), not an App Store listing. The app shell can open offline, but live prices, market snapshots and AI forecasts still require an internet connection. iOS may keep some browser chrome or apply platform-specific install behavior.

## What is included
- Multi-timeframe BTC context: 5m, 15m, 1h, 4h and 1d.
- Experimental ML forecasts for 15m, 30m, 1h and 4h, including a Random Forest, gradient-boosting, neural MLP and optional XGBoost ensemble.
- Chronological holdout validation and conservative WAIT gates. Model scores are not calibrated win probabilities and are not guarantees of future performance.
- Public derivatives monitor: funding, open interest where available, executed taker-flow feed where published, rolling CVD proxy, order-book imbalance and large-trade size alerts.
- Data-health audit for 5m candles, five-timeframe coverage, funding, OI, taker flow, CVD, order book, news freshness, Trump-post archive availability, cross-exchange perpetual comparison and liquidation monitoring. The GitHub Actions liquidation collector samples the Binance force-order WebSocket for 10 seconds per scheduled run (about every 15 minutes); it is not continuous and misses events between samples. A genuinely continuous WebSocket requires the separate always-on `live_runner.py` host. Missing/stale feeds are shown as unavailable, not guessed.
- Market-risk headline scanning plus an independent archive RSS of public @realDonaldTrump Truth Social posts (`trumpstruth.org/feed`). Relevant posts/headlines trigger at least a 30-minute WAIT lock. The archive is third-party, not an official Truth Social API; it may lag or omit deleted/visual-only content.
- Dashboard BTC price refreshes in the open browser every 5 seconds. This is client-side price refresh only; it is not a 5-second AI retraining or server-side signal cycle.
- Full dashboard indicators refresh every 30 seconds, while the published server-side AI output follows GitHub Actions on a best-effort 15-minute market-data schedule (AI retraining runs separately) and can be delayed.

## Important limitations
- Derivatives data uses public perpetual-Futures exchange endpoints with provider fallback (Binance, Bybit, Bitget and OKX where reachable); the selected venue can vary by runner-region access and field availability. Verify the displayed source and compare with Binance execution price/liquidity.
- Large trades are size alerts, not verified whale identities.
- CVD is an estimate from recent public trades, not exchange-wide consolidated order flow.
- Experimental AI forecasts may return WAIT frequently when validation quality is weak. That is intentional.
- Max leverage guidance is 10x only as a ceiling, not a recommendation. Leverage magnifies losses; fees, funding, slippage and liquidation can make outcomes worse.
- No model accuracy or profit is guaranteed. Review all signals manually and use independent risk controls.

## Alerts
Telegram integration is intentionally not part of this setup. The dashboard remains signal-only and does not require bot tokens, exchange API keys, or paid alert services.

## 24/7 hosting
`live_runner.py` provides a persistent liquidation WebSocket and a periodic monitor, but it must run on an always-on host. See `DEPLOY_VPS.md`. GitHub Actions schedules are best-effort and do not provide a guaranteed 5-second backend. The current live runner polls the REST/news monitor about once per minute to avoid excessive public API requests; the dashboard price itself refreshes every 5 seconds in an open browser. The dashboard distinguishes short GitHub Actions samples from a genuinely connected, recently active continuous liquidation stream when `live_runner.py` is hosted.

## Automation
GitHub Actions publishes the public market snapshot on a best-effort 15-minute schedule; AI training runs separately. These schedules can be delayed. You can inspect run history in the repository's **Actions** tab.


## Institutional Intelligence (v6 layer)

Open the additional [Institutional Intelligence dashboard](./institutional.html) for:
- Market regime classification (bull trend, bear trend, range/chop, transition, high volatility).
- Estimated 15m candle-volume profile, POC, 70% value area and VWAP.
- Recent 15m liquidity sweep / breakout and false-breakout context.
- Public Deribit BTC options snapshot (put/call open interest, volume, implied volatility and approximate max pain).
- 14-day hourly BTC return correlations with Nasdaq, DXY and Gold (where public feeds are available).
- 15m, 30m and 1h research-only next-move scenarios.
- A strict signal-quality gate.

The quality gate blocks LONG/SHORT until all checks pass: the AI model beats the majority-class baseline, macro-F1 is at least 30, at least 30 shadow forecasts are closed and pass validation, and a cost-aware 300+ candle holdout backtest is completed with positive net return. This is deliberately conservative and may keep the system on WAIT for an extended period. Options and macro feeds are optional context and are marked unavailable if a public source fails.

This layer is public-data research only. It does not place orders, and scenario scores are not calibrated probabilities or profit guarantees. GitHub Actions refreshes market and signal snapshots on a best-effort 15-minute schedule, subject to delays; AI training runs separately. It is not a five-second server-side AI engine.
