# Phase 7 — V1 Freeze: 14-Day Paper Trading Runbook

**Period:** 14 consecutive days of real-market, simulated-capital operation  
**Gate:** All three bots must show `BOT_MODE = PAPER` and no LIVE orders executed  
**Goal:** Confirm that signal quality, risk controls, and system stability hold under real market conditions before any transition to live capital

---

## 1. Starting the Validation Period

Set the start timestamp once, immediately before the first full trading day:

```bash
# In Railway → Variables (or .env for local dev)
PAPER_TRADING_START=2026-07-01T00:00:00Z
```

Check the countdown at any time:

```bash
GET /api/v1/validation/status
# Returns: days_elapsed, days_remaining, all_bots_in_paper_mode, per-bot PnL
```

---

## 2. Daily Operator Checklist

Run these checks each morning before the market opens:

| # | Check | How | Pass Criterion |
|---|-------|-----|----------------|
| 1 | All bots in PAPER mode | `GET /api/v1/validation/status` → `all_bots_in_paper_mode` | `true` |
| 2 | Circuit breaker healthy | Same endpoint → `circuit_breaker.state` | `ACTIVE` |
| 3 | No emergency stop | `GET /api/v1/state` → `risk.emergency_stop` | `false` |
| 4 | Scanner producing signals | `GET /api/v1/state` → `scanner.signals` count | > 0 after bootstrap |
| 5 | App health | `GET /health` | `{"status":"ok"}` with HTTP 200 |
| 6 | No critical errors in logs | Railway → Logs | No `CRITICAL` or unhandled exceptions |

---

## 3. Weekly Review (Days 7 and 14)

Pull the full stats export and review:

```bash
GET /api/v1/export/stats.json
GET /api/v1/export/trades.csv
GET /api/v1/export/signals.csv
```

Metrics to evaluate:

| Metric | Minimum Bar | Notes |
|--------|-------------|-------|
| Scanner win rate | ≥ 50% | Signal accuracy over evaluated signals |
| VGX total PnL | > −5% of virtual balance | Drawdown within acceptable range |
| MTB / PMB total PnL | > −5% of starting cash | Per-bot drawdown |
| Circuit breaker trips | 0 | Any trip must be investigated before proceeding |
| System uptime | ≥ 99% | No more than ~3.5 hours downtime over 14 days |

---

## 4. Incident Protocol

### Circuit Breaker Trip
1. Note the timestamp and `circuit_breaker.total_breaks` from `/api/v1/validation/status`
2. Identify trigger: daily / weekly / monthly limit or emergency stop
3. Manually reset via Telegram `/reset` command or `/api/v1/state` inspection
4. Document the cause in `INCIDENT_LOG.md` (create if absent)
5. If three or more trips occur, **pause the validation period** and investigate before resuming

### Critical Log Error
1. Capture the full stack trace from Railway logs
2. Fix the root cause in a hotfix branch
3. If the fix touches trading logic, restart the validation period clock from Day 1

### Bot in LIVE Mode Detected
1. **Stop all bots immediately** — cancel all running Railway services
2. Verify no real orders were placed on CoinDCX (check API trade history)
3. Reset `VGX_BOT_MODE`, `MTB_BOT_MODE`, `PMB_BOT_MODE` to `PAPER` and redeploy
4. Restart the validation period from Day 1

---

## 5. Validation Pass Criteria (Day 14)

All of the following must be true to declare V1 Freeze complete:

- [ ] `validation_complete: true` from `/api/v1/validation/status`
- [ ] `all_bots_in_paper_mode: true` every day of the period
- [ ] Zero circuit breaker emergency stops
- [ ] Scanner win rate ≥ 50% at day 14 review
- [ ] No restarted validation period (no incidents requiring clock reset)
- [ ] All 22 unit tests passing: `python -m pytest tests/ -q`

---

## 6. Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `PAPER_TRADING_START` | *(unset)* | ISO-8601 UTC start of 14-day window |
| `VGX_BOT_MODE` | `PAPER` | VGX trading mode — never set to `LIVE` during validation |
| `MTB_BOT_MODE` | `PAPER` | MTB trading mode |
| `PMB_BOT_MODE` | `PAPER` | PMB trading mode |
| `VGX_ENABLED` | `true` | Enable/disable VGX background loop |
| `MTB_ENABLED` | `false` | Enable/disable MTB background loop |
| `PMB_ENABLED` | `false` | Enable/disable PMB background loop |
| `DASHBOARD_API_KEY` | *(required)* | API key for dashboard routes (all non-`/health` routes) |
| `COINDCX_CANDLES_URL` | CoinDCX API | Override candles endpoint (useful for testing) |

---

## 7. Frozen API Surface (V1)

The following endpoints are considered stable and must not change signature during the validation period:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health probe — always unauthenticated |
| `/api/v1/state` | GET | Full unified dashboard state |
| `/api/v1/stats/unified` | GET | Win rate, leaderboard, per-bot PnL |
| `/api/v1/stats/leaderboard` | GET | Coin leaderboard |
| `/api/v1/stats/telegram` | GET | Compact Telegram-ready summary |
| `/api/v1/validation/status` | GET | 14-day paper trading tracker |
| `/api/v1/alerts` | GET | Alert notification center |
| `/api/v1/errors` | GET | Error log viewer |
| `/api/v1/export/signals.json` | GET | All scanner signals as JSON |
| `/api/v1/export/signals.csv` | GET | All scanner signals as CSV |
| `/api/v1/export/trades.csv` | GET | All trades (VGX/MTB/PMB) as CSV |
| `/api/v1/export/stats.json` | GET | Full stats snapshot as JSON |
| `/api/scanner/refresh` | POST | Trigger immediate scanner refresh |
| `/api/watchlist/add` | POST | Add coin to scanner watchlist |
| `/api/watchlist/remove` | POST | Remove coin from scanner watchlist |
| `/api/supported-coins` | GET | List of supported coins on CoinDCX |

> **Code Freeze**: No new endpoints, no signature changes, no dependency upgrades without explicit approval during the 14-day validation window.

---

## 8. Post-Validation

Once the 14-day pass criteria are met:

1. File a completion report documenting all metrics
2. Tag the codebase: `git tag v1.0-validated`
3. Only then consider Phase 8 (Live Capital Readiness) planning
