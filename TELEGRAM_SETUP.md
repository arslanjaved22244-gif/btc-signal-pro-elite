# Telegram alert setup

The bot sends manual-trading notifications only. It never places exchange orders.

## 1. Create a Telegram bot
1. In Telegram, open the verified **@BotFather** account.
2. Send `/newbot` and follow the prompts.
3. Copy the bot token. Keep it private; do not paste it into this repository or a public issue.

## 2. Get your chat ID
1. Open your new bot in Telegram and press Start.
2. Send it a message such as `hello`.
3. Open this URL in a browser, replacing TOKEN with your bot token:
   `https://api.telegram.org/botTOKEN/getUpdates`
4. Find `message.chat.id` in the response. Keep it private.

## 3. Add GitHub Actions secrets
Open the repository:
`https://github.com/arslanjaved22244-gif/btc-signal-pro-elite/settings/secrets/actions`

Choose **New repository secret** and add these two secrets exactly:

- Name: `TELEGRAM_BOT_TOKEN` — Value: the token from BotFather.
- Name: `TELEGRAM_CHAT_ID` — Value: your Telegram chat ID.

Do not send either value in ChatGPT or commit them to a file.

## 4. Test
In GitHub, open **Actions → BTC Futures Market Data → Run workflow**. After the workflow completes, check the dashboard's **Telegram alerts** status and your Telegram chat. A notification is sent only when a new eligible LONG/SHORT signal or early-setup watch exists; WAIT does not generate an entry alert.

If it says `NOT CONFIGURED`, recheck both secret names. If it says `NO_NEW_LONG_SHORT_SIGNAL`, the credentials were read but there was no new alert to send.

## Safety
Alerts are informational and signal-only. Check the live market, news, entry, stop, fees and funding yourself. No order is submitted.
