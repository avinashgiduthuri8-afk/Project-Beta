# Project Beta v1 Verification

The original v1 boundary was verified locally on 2026-10-11. The scanner and
risk expansion was deployed in paper-only mode on 2026-10-16; no broker order
or Git push was performed.

## Delivered boundary

- Three services in order: Scanner (stages 1–5), Paper Trading (6–7), and AI
  Learner (8–10).
- NSE/BSE cash equities only, INR accounting, and positive whole-share sizes.
- Yahoo delayed public data by default; optional authenticated Dhan v2 market
  feed with public instrument-master resolution.
- Dhan is market-data-only. No order endpoint or broker execution package is
  present.
- Scanner owns market data and qualified signals. Paper owns cash, positions,
  and journals. Learner owns learning audits; cross-service mounts are read-only.
- Persistent signal consumption prevents duplicate positions and cash debits
  after process restarts.

## Reproducible checks

Run from the repository root:

```powershell
python -m compileall -q services shared tests deploy
python -m pytest tests -q
ruff check services shared tests deploy --select=E9,F63,F7,F82 --target-version=py312
git diff --check
```

The verified suite contains 47 tests, including a deterministic offline flow
through stages 1–10, Dhan binary ticker decoding and generation invalidation,
paper restart idempotency, fail-closed learning, service ownership, and
repository hygiene.

## External prerequisites

Yahoo mode needs no key. Dhan mode requires Beta-specific `DHAN_CLIENT_ID` and
`DHAN_ACCESS_TOKEN` values plus network access. The implementation follows the
official Dhan v2 market-feed contract (`wss://api-feed.dhan.co`) and compact
instrument master. Credentials are never copied from Project Alpha and no
credential values are committed.

## Resource and deployment status

Systemd caps the combined Beta fleet for a 1-vCPU, 2-GB-RAM, 20-GB-SSD server.
The verified deployment uses `/opt/project-beta`, a dedicated `project-beta`
account, mode-0600 environment file, mode-0640 database, and three `beta-*`
units. Project Alpha services were not restarted or modified.

## 2026-10-16 scanner and risk expansion

- Scanner, Paper, and Learner use independent connections to one shared
  `data/project_beta.db` with WAL, foreign keys, busy timeout, and versioned
  migrations.
- Scanner enforces the INR 250 ceiling, penny-stock evidence gates,
  deterministic top-50 ranking, audited rejection/qualification decisions, and
  a top-10 publication cap.
- Paper enforces INR 1,000 all-in per trade and daily loss warning/block/stop
  levels of INR 750/900/1,000. Emergency-stop state survives process restart.
- Learner accepts only allowlisted finite calibration values; it cannot change
  price, allocation, daily loss, leverage, shorting, or live-mode protections.
- Offline end-to-end tests use three connections to one temporary SQLite file;
  they do not call a broker or place live orders.
