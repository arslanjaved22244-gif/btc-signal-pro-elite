# Persistent 24/7 runner

The repository includes a Docker image for the signal-only runner.

## Run on an always-on VM or container host

```sh
docker build -t btc-signal-pro-elite .
docker run -d --restart unless-stopped -p 8080:8080 -v btc-signal-data:/app -e PORT=8080 --name btc-signal-pro-elite btc-signal-pro-elite
```

Open `http://YOUR-HOST:8080/healthz`.

- HTTP 200: runner process is alive and the Binance liquidation WebSocket is connected.
- HTTP 503: WebSocket is disconnected or reconnecting.
- The response includes the last message timestamp and latest socket error.

Mount persistent storage at `/app` so the runner's JSON state survives container restarts.

## Hosting truth

GitHub Pages is static and GitHub Actions scheduled workflows are not persistent processes. Continuous WebSocket monitoring requires an always-on VM/container host. This repository is packaged for deployment, but actual deployment requires an authorized host account and its credentials. Free services that sleep or scale to zero do not provide 24/7 uptime.

## Limitations and safety

- Manual signals only; no exchange keys and no order placement.
- Binance `forceOrder` is a sampled public liquidation stream, not a complete market-wide liquidation tape.
- A connected socket with zero messages does not prove that no liquidations happened.
- The public dashboard has no authentication; do not put private information in its served files.
