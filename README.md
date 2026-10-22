# Project Beta

Project Beta v1 is an offline-safe, paper-only Indian cash-equity platform for
NSE and BSE stocks. Its service order is Scanner → Paper Trading → AI Learner,
covering one explicit ten-stage workflow.

| Service | Stages | Owns | Reads |
|---|---:|---|---|
| `beta-scanner` | 1–5 | market quotes, candles, qualified signals | TradingView, Yahoo, or optional Dhan market data |
| `beta-paper` | 6–7 | paper cash, positions, fills, journals | Scanner database (read-only) |
| `beta-learner` | 8–10 | diagnoses, validations, promoted calibrations | Scanner and Paper databases (read-only) |

TradingView and Yahoo public endpoints provide keyless market access by default. Optional Dhan credentials
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

Run all three services concurrently in a single server process with unified Telegram C2:

```powershell
python main.py
# or
python -m services.main
```

Alternatively, individual services can be started independently if desired:

```powershell
python -m services.scanner.main
python -m services.paper_bot.main
python -m services.learner.main
```

## Market data providers

Project Beta supports multiple keyless and authenticated market data providers:
- `MARKET_DATA_PROVIDER=TRADINGVIEW`: High-speed real-time quotes, technical indicator aggregations (RSI, ATR, EMAs), and sub-₹250 market screening via TradingView's Indian equities screener API. Runs keyless by default (no credentials required), with optional `TRADINGVIEW_API_KEY` support.
- `MARKET_DATA_PROVIDER=YAHOO`: Default keyless public delayed market data.
- `MARKET_DATA_PROVIDER=DHAN`: Optional Dhan real-time market data feed.

The included systemd units run the three services as the unprivileged
`project-beta` account with bounded CPU and memory. The verified paper release
is installed under `/opt/project-beta`; deployment remains a deliberate manual
operation and this repository never pushes or uploads itself.

## Scanner and paper-risk rules

Scanner, Paper, and Learner open separate WAL-enabled connections to the same
`data/project_beta.db`. Scanner accepts only finite prices satisfying
`0 < price <= INR 250`; stocks below INR 1 use stricter penny-stock liquidity
and spread gates. Ranking is deterministic, only the top 50 receive deep
strategy evaluation, and at most 10 signals are published per cycle. Public or
delayed data can be stale or incomplete, so no precise movement probability is
claimed.

Paper independently caps each new position at INR 1,000 including estimated
buy charges. Daily loss includes realized P&L, unrealized P&L, and charges: warn
at INR 750, block new entries at INR 900, and persist an emergency stop at INR
1,000. The starting portfolio limits are three open positions, one per sector,
and three consecutive losing exits.

Before migration or deployment, back up the shared database with SQLite's
backup API and verify `PRAGMA quick_check`. Restarting a process or running the
Learner cannot clear a same-day emergency stop. Credential examples are blank;
rotate any credential previously exposed outside a secret manager.
