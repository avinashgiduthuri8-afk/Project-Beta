# PROJECT-ALPHA Structure

## Runtime flow

```text
CoinDCX market data
        |
        v
services/scanner
        |
        +--> shared event, signal, risk, market, and Telegram components
                 |                         |
                 v                         v
        services/paper_bot        services/live_bot
        simulated execution       authenticated execution
```

## Source ownership

- `services/scanner/`: candle ingestion, market scanning, signal generation, and scanner health.
- `services/paper_bot/`: paper orders, portfolio allocation, risk checks, and simulated position exits.
- `services/live_bot/`: CoinDCX order submission, HMAC signing, reconciliation, position locks, and live capital guards.
- `shared/`: reusable core types, persistence, exchange adapters, market feeds, execution logic, risk logic, background workers, and Telegram controls.
- `tests/`: tests partitioned by service plus shared-domain integration tests.
- `deploy/`: the three systemd units, Docker assets, environment examples, and operational scripts.

## Boundary rules

1. A service may import `shared` code but may not import another service.
2. Paper and live execution must not share a database or credentials.
3. Runtime databases, overrides, caches, logs, and secrets are not source files and must remain untracked.
4. New cross-service behavior belongs in `shared/` only when at least two services consume it.
5. Deployment definitions must refer only to existing service entrypoints.
