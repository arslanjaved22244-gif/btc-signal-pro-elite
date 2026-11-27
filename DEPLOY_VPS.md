# 24/7 VPS Deployment

## What this runs
- Python monitor loop approximately once per minute (public derivatives/news feeds).
- Persistent Binance USD-M force-order WebSocket with automatic reconnect.
- Dashboard served on port 8080 and refreshed by its page scripts.
- Signal-only. No exchange API keys, no order placement, no Telegram.

## Recommended VPS
Ubuntu 24.04 LTS, 2 vCPU / 2 GB RAM or more. DigitalOcean Basic 2 GB currently lists at $12/month; check checkout for taxes and region availability.

## One-time setup
1. Create an Ubuntu 24.04 VPS and note its public IPv4 address.
2. SSH into it as root (or use the provider's web console).
3. Run:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/arslanjaved22244-gif/btc-signal-pro-elite/main/deploy-vps.sh -o deploy-vps.sh
   bash deploy-vps.sh
   ```
4. Open `http://YOUR_SERVER_IP:8080/` in Safari.

## Operations
- Check: `cd /opt/btc-signal-pro-elite && docker compose ps`
- Logs: `cd /opt/btc-signal-pro-elite && docker compose logs -f --tail=100`
- Restart: `cd /opt/btc-signal-pro-elite && docker compose restart`
- Update: `cd /opt/btc-signal-pro-elite && git pull --ff-only && docker compose up -d --build`

## Important
- Keep port 8080 open in the VPS provider firewall. The page is plain HTTP; do not put secrets or private information in it. Add HTTPS/domain protection before treating it as a private dashboard.
- No Binance/Bybit/OKX API credentials are required for public market data.
- The monitor polls its REST/news sources roughly every 60 seconds; the liquidation stream is persistent while the VPS is running. Public liquidation streams are not a complete market-wide tape.
- This VPS service does not retrain the advanced ML model continuously; the existing GitHub Actions AI workflow remains separate.
- The existing GitHub Actions monitor may remain enabled as a fallback, but avoid relying on its sampled liquidation file as live data.
