# BTC Signal Pro Elite

Phone-friendly BTC/USDT signal dashboard. It is **signal-only**: it does not connect to your Binance account, place orders, or require exchange API keys.

## Open the dashboard
GitHub Pages: https://arslanjaved22244-gif.github.io/btc-signal-pro-elite/

## What is included
- Multi-timeframe BTC context: 5m, 15m, 1h, 4h and 1d.
- Experimental ML forecasts for 15m, 30m, 1h and 4h, including a Random Forest, gradient-boosting, neural MLP and optional XGBoost ensemble.
- Chronological holdout validation and conservative WAIT gates. Model scores are not calibrated win probabilities and are not guarantees of future performance.
- Public derivatives monitor: funding, open interest where available, CVD proxy, taker buy ratio, order-book imbalance and large-trade size alerts.
- Headline-based news safety pause of at least 30 minutes. Headlines can extend the pause while they remain in the recent feed. This is not a direct Trump/X feed and cannot guarantee detection of every breaking event or public post.
- Dashboard BTC price refreshes in the open browser every 5 seconds; this is a client-side price refresh, not a 5-second AI retraining or server-side signal cycle.
- Full dashboard indicators refresh every 30 seconds, while the published server-side AI output follows GitHub Actions on a best-effort 5-minute schedule and can be delayed.

## Important limitations
- Derivatives data comes from public exchange endpoints (Bybit with OKX fallback); individual fields can be unavailable during API outages or when the provider does not publish the requested history.
- Large trades are size alerts, not verified whale identities.
- CVD is an estimate from recent public trades, not exchange-wide consolidated order flow.
- Experimental AI forecasts may return WAIT frequently when validation quality is weak. That is intentional.
- Max leverage guidance is 10x only as a ceiling, not a recommendation. Leverage magnifies losses; fees, funding, slippage and liquidation can make outcomes worse.
- No model accuracy or profit is guaranteed. Review all signals manually and use independent risk controls.

## Automation
The repository uses GitHub Actions to refresh the AI output and public market snapshot. You can inspect run history in the repository's **Actions** tab. No Telegram setup is required for the dashboard.
