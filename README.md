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
- Public derivatives monitor: funding, open interest where available, CVD proxy, taker buy ratio, order-book imbalance and large-trade size alerts.
- Market-risk headline scanning plus an independent archive RSS of public @realDonaldTrump Truth Social posts (`trumpstruth.org/feed`). Relevant posts/headlines trigger at least a 30-minute WAIT lock. The archive is third-party, not an official Truth Social API; it may lag or omit deleted/visual-only content.
- Dashboard BTC price refreshes in the open browser every 5 seconds. This is client-side price refresh only; it is not a 5-second AI retraining or server-side signal cycle.
- Full dashboard indicators refresh every 30 seconds, while the published server-side AI output follows GitHub Actions on a best-effort 5-minute schedule and can be delayed.

## Important limitations
- Derivatives data comes from public exchange endpoints (Bybit with OKX fallback); individual fields can be unavailable during API outages or when the provider does not publish the requested history.
- Large trades are size alerts, not verified whale identities.
- CVD is an estimate from recent public trades, not exchange-wide consolidated order flow.
- Experimental AI forecasts may return WAIT frequently when validation quality is weak. That is intentional.
- Max leverage guidance is 10x only as a ceiling, not a recommendation. Leverage magnifies losses; fees, funding, slippage and liquidation can make outcomes worse.
- No model accuracy or profit is guaranteed. Review all signals manually and use independent risk controls.

## Telegram alerts
Optional signal-only Telegram alerts are supported. Add two repository Actions secrets: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Never put these values in source code or send them in chat. In GitHub, open Settings → Secrets and variables → Actions → New repository secret. The dashboard shows the latest alert status in the derivatives/news panel. See `TELEGRAM_SETUP.md`.

## 24/7 hosting
`live_runner.py` provides a persistent liquidation WebSocket and a periodic monitor, but it must run on an always-on host. See `DEPLOY_VPS.md`. GitHub Actions schedules are best-effort and do not provide a guaranteed 5-second backend. The current live runner polls the REST/news monitor about once per minute to avoid excessive public API requests; the dashboard price itself refreshes every 5 seconds in an open browser.

## Automation
GitHub Actions refreshes the advanced AI output and public market snapshot on best-effort schedules (normally 5 minutes). You can inspect run history in the repository's **Actions** tab.


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

This layer is public-data research only. It does not place orders, and scenario scores are not calibrated probabilities or profit guarantees. GitHub Actions refreshes server snapshots on its schedule (normally about every five minutes, subject to delays); it is not a five-second server-side AI engine.
