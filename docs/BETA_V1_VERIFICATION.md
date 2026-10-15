# Project Beta v1 Verification

Verified locally on 2026-10-11. No VPS connection, upload, deployment, broker
order, or Git push was performed.

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

Local Compose and systemd definitions cap the combined fleet for a future
1-vCPU, 2-GB-RAM, 20-GB-SSD server and rotate container logs. These are design
artifacts only. Server provisioning remains explicitly deferred until a new
server is supplied and separately approved.
