# Indian market adaptation

Project Beta separates market data from execution:

1. `shared.market.IndianStockPublicClient` provides keyless, delayed NSE/BSE
   quote and candle data for scanning, research, and paper trading.
2. `shared.execution.brokers.PaperBroker` is the default execution target.
3. `ZerodhaBroker` and `DhanBroker` implement authenticated order placement.
4. `IndianBrokerManager` bridges the cloned Alpha router contract to those
   broker adapters.

## Credential boundary

Zerodha requires an API key and access token for order and market APIs. DhanHQ
requires an access token on API calls, and order placement can require static-IP
whitelisting. Consequently, there is no safe or supported anonymous live-order
mode. Project Beta never falls back from a failed live configuration to another
real broker; it fails closed.

## Public data boundary

The keyless provider is not an exchange-authorized low-latency feed. Responses
can be delayed, unavailable, or adjusted. It is intentionally isolated from the
live order adapter. Use the selected broker's authenticated quote/WebSocket feed
before enabling real-money automation.

## Symbols and orders

- Canonical symbol: `EXCHANGE:TRADINGSYMBOL`.
- Zerodha accepts the canonical trading symbol directly.
- Dhan resolves the trading symbol to `securityId` using Dhan's public compact
  instrument master before submitting an authenticated order.
- Quantity is always a whole share for cash equities.
- `CNC` is the default product; set `PRODUCT_TYPE=MIS` only after validating
  broker-specific intraday and auto-square-off behavior.

## Charges

Backtest and journal charges are estimates. Rates and broker pricing can change.
Always reconcile against the broker contract note. The engine does not calculate
STCG, LTCG, slab, audit, or other income-tax liability.
