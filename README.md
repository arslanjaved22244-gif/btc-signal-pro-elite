# BTC Signal Pro Elite

Signal-only BTC/USDT Futures dashboard and scheduled monitor.

## Scheduled monitor
GitHub Actions runs `bot/monitor.py` approximately every 5 minutes and can also be started manually from the Actions tab.

### Telegram setup (required for phone alerts)
In the repository on GitHub:
1. Open **Settings → Secrets and variables → Actions**.
2. Add repository secret `TELEGRAM_BOT_TOKEN` (from your Telegram bot created with BotFather).
3. Add repository secret `TELEGRAM_CHAT_ID` (your private chat ID; start the bot and send it a message first).
4. Open **Actions → BTC Signal Pro Elite Monitor → Run workflow** to test.

Never put the bot token in `index.html`, source code, screenshots, or public messages. If the repository is public, keep all credentials in GitHub Secrets.

## Monitor behavior and limitations
- Uses public Binance USD-M Futures market endpoints; no Binance API key and no trading permissions are used.
- Checks BTC price, 15m/1h/4h/1d EMA structure, RSI, ATR, funding, open interest, taker buy volume, recent aggregate trades (CVD proxy), and order-book imbalance.
- Sends Telegram alerts when the signal state changes. Signals are LONG / SHORT / WAIT and are for manual review only. No orders are placed.
- Google News RSS is used as a headline-based risk filter. A recent relevant headline starts a 30-minute safety pause; a headline still appearing in the recent feed may extend it. This is not a direct X/Twitter feed and cannot guarantee detection of every breaking event or public post.
- GitHub scheduled workflows are best-effort and may be delayed; this is not a continuously running 24/7 server or guaranteed real-time alert system.
- The monitor is rule-based, not a proven predictive AI model. Backtests or experimental dashboard estimates do not guarantee future results.
- Max leverage guidance is 10x. Leverage magnifies losses; stop execution can differ due to slippage, fees, funding, and liquidation risk.
