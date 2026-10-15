# Project Beta

Project Beta v1 is an offline-safe, paper-only Indian cash-equity platform for
NSE and BSE stocks. Its service order is Scanner → Paper Trading → AI Learner,
covering one explicit ten-stage workflow.

| Service | Stages | Owns | Reads |
|---|---:|---|---|
| `beta-scanner` | 1–5 | market quotes, candles, qualified signals | Yahoo or optional Dhan market data |
| `beta-paper` | 6–7 | paper cash, positions, fills, journals | Scanner database (read-only) |
| `beta-learner` | 8–10 | diagnoses, validations, promoted calibrations | Scanner and Paper databases (read-only) |

Yahoo delayed public data is the keyless default. Optional Dhan credentials
enable market data only. There is no broker order client, live service, crypto,
CoinDCX, Zerodha, or VPS action in v1. Live trading is deferred to a separately
designed v2.

Canonical instruments are `NSE:RELIANCE` and `BSE:500325`; quantities are
positive whole shares and all accounting is INR. Paper costs are labelled
estimates and include brokerage, STT, exchange and SEBI charges, GST, stamp
duty, and deterministic slippage; income tax is excluded.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item config\beta.env.example .env
python -m pytest tests -q
```

Run services in this order in separate terminals:

```powershell
python -m services.scanner.main
python -m services.paper_bot.main
python -m services.learner.main
```

The included Compose and systemd files are local/future-server artifacts only.
They target a combined envelope of roughly 1 vCPU, 2 GB RAM, and 20 GB SSD,
with bounded logs and read-only cross-service data mounts. Nothing in this
repository uploads or deploys to a server.
