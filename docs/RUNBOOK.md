# PROJECT-ALPHA Production Runbook

## Runtime fleet

PROJECT-ALPHA runs two active production services:

- `alpha-scanner.service` produces market signals.
- `alpha-paper.service` performs simulated execution.

*Note: The Live Bot (`alpha-live.service`) is inactive and explicitly excluded from active deployment.*

## Required configuration

| Variable | When required | Description |
|---|---|---|
| `DEPLOYMENT_MODE` | Always | `PAPER` |
| `OPERATOR_SECURITY_PASSWORD` | Recommended | Confirms protected operator transitions through Telegram. |
| `GEMINI_API_KEY` | Optional | Enables Gemini signal evaluation. |
| `ALERT_BOT_TOKEN` | Optional | Telegram alert/control bot token. |
| `ALERT_CHAT_ID` | Optional | Authorized Telegram destination. |
| `TELEGRAM_ALLOWED_CHAT_IDS` | Recommended | Operator allowlist for Telegram commands. |

Use the service-specific examples in `deploy/env/`. Never commit real credentials.

## Operational service checks

Useful service checks:

```bash
systemctl status alpha-scanner alpha-paper --no-pager
journalctl -u alpha-scanner -n 100 --no-pager
journalctl -u alpha-paper -n 100 --no-pager
```

## Telegram controls

Common controls include `/status`, `/positions`, `/trades`, `/pnl`, `/capital`, `/pause`, `/resume`, `/emergency_stop`, `/setamount`, and `/reconcile`.

Live mode changes require explicit confirmation. If `OPERATOR_SECURITY_PASSWORD` is configured, include the password only through the protected operator workflow and avoid recording it in shell history or logs.

## Protective exits

CoinDCX spot execution does not provide this application with a native bracket-order guarantee. The services monitor stop loss, take profit, and trailing exits. If a live process is down, open exchange positions can remain exposed until monitoring resumes or an operator closes them.

## Emergency procedure

1. Send `/emergency_stop` through the authorized Telegram control channel.
2. Confirm no new orders are being submitted.
3. Inspect open exchange positions independently of local state.
4. Close or protect positions manually when required.
5. Preserve logs and database files for reconciliation before restarting.

## Return to paper mode

Set `DEPLOYMENT_MODE=PAPER`, keep live trading disabled, and restart only the intended services. Verify service status and database selection before resuming automated operation.
