# Project: PROJECT-ALPHA Execution Module (E) Hardening

## Architecture
- **Package**: `execution`
- **Core Components**:
  - `execution/trading/subaccount_manager.py`: Manages subaccounts, master client, API credential resolution, order placement (`place_order`), cancellation, and CoinDCX REST interaction.
  - `execution/reconciliation.py`: Position & order reconciliation service reconciling local SQLite state (`PositionRepository`, `OrderRepository`) with authoritative exchange orders.
  - `execution/auto_trader.py`: `AutoTradeRouter` routing verified scanner signals to execution strategies under risk limits and single-tranche caps.
  - `execution/service.py`: High-level `TradingService` coordinating EventBus signals (`SIGNAL_GENERATED` -> AI confirmation -> `TRADE_APPROVED`), trade execution, and lifecycle management.
- **Data Flow**:
  1. Scanner generates signal -> AIIntelligenceService confirms -> RiskService approves (`EventType.TRADE_APPROVED`).
  2. `AutoTradeRouter` / `TradingService` validates verified signal & risk authorization, clamps sizing to ₹200 / `ORDER_SIZE_INR`, and routes order.
  3. `SubaccountManager` places order without mock credentials, returning structured result with `exchange_order_id`.
  4. `ReconciliationService` executes safe 3-stage `FLAG -> VERIFY -> RECONCILE` protocol, preserving active disaster stop-losses and resting limit orders.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1 | Eliminate mock API credentials in subaccount manager | Remove `mock_master_key_alpha12345` and `mock_master_secret_alpha67890abcdef`, implement fail-closed live credentials check | M1 | ORIGINAL_REQUEST §R1 |
| F2 | Deduplicate `"status"` evaluations | Remove duplicate `"status"` keys at lines 305-306 and 459-460 in `subaccount_manager.py` | M1 | ORIGINAL_REQUEST §R1 |
| F3 | Eliminate unreachable return statements | Consolidate duplicate returns at lines 344-349 and 478-483 to return both `order` and `exchange_order_id` | M1 | ORIGINAL_REQUEST §R1 |
| F4 | Prune shadowed methods and fix config scope | Remove dead shadowed `cancel_order`, `get_order_status`, `get_account_balances` and fix `trading_cfg` NameError in `subaccount_manager.py` | M1 | Survey Explorer 1 |
| F5 | Safe FLAG stage in reconciliation | Flag mismatched exchange orders as pending reconciliation without immediate cancellation | M2 | ORIGINAL_REQUEST §R2 |
| F6 | Safe VERIFY stage in reconciliation | Perform retries and query authoritative exchange status before considering any action | M2 | ORIGINAL_REQUEST §R2 |
| F7 | Safe RECONCILE stage in reconciliation | Synchronize local state with exchange fills, only closing/cancelling after verified orphan status | M2 | ORIGINAL_REQUEST §R2 |
| F8 | Disaster stop-loss and limit order protection | Index `pos.stop_loss_order_id` in `pos_by_ex_id` and check `OrderRepository` so active protective orders are never cancelled | M2 | Survey Explorer 2 |
| F9 | Remove legacy autonomous background trade triggers | Deprecate raw `SIGNAL_GENERATED` handler in `AutoTradeRouter`, gate execution strictly on verified signals & risk approval | M3 | ORIGINAL_REQUEST §R3 |
| F10 | Enforce strict ₹200 / `ORDER_SIZE_INR` micro-tranche sizing | Update default fallback from 500 to 200 INR and clamp trade amounts in `AutoTradeRouter` | M3 | ORIGINAL_REQUEST §R3, GEMINI.md §3 |
| F11 | Enforce `BotMode.PAPER` invariants | Ensure `AutoTradeRouter` respects paper mode without direct unrecorded live exchange calls | M3 | Survey Explorer 3, GEMINI.md |
| F12 | Comprehensive test suite verification | Verify `test_execution_master.py`, `test_safety_invariants.py`, and `test_bot_pipeline.py` pass cleanly | M4 | ORIGINAL_REQUEST §Acceptance Criteria |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Subaccount Manager Hardening | `execution/trading/subaccount_manager.py` (F1, F2, F3, F4) | none | DONE |
| M2 | Safe Reconciliation Protocol | `execution/reconciliation.py`, `execution/service.py` (F5, F6, F7, F8) | M1 | DONE |
| M3 | AutoTrader Signal-Driven Alignment | `execution/auto_trader.py` (F9, F10, F11) | M1 | DONE |
| M4 | Master Test Suite Pass & Adversarial Hardening | Full test suites, white-box stress testing, integrity audit (F12) | M1, M2, M3 | DONE |

## Interface Contracts
### `SubaccountManager.place_order` -> Caller (`TradingService` / `AutoTradeRouter`)
- **Return Value**:
  ```python
  {
      "success": True,
      "exchange_order_id": str,
      "order": dict,
  }
  ```
- **Error Value**:
  ```python
  {
      "success": False,
      "error": str,  # e.g., "MISSING_CREDENTIALS", "TIMEOUT"
      "message": str,
      "requires_reconciliation": bool,
  }
  ```

### `ReconciliationService` ↔ `PositionRepository` & `OrderRepository`
- `ReconciliationService` receives `position_repo: PositionRepository` and optional `order_repo: OrderRepository`.
- Indices built:
  - `pos_by_ex_id`: maps `pos.exchange_order_id` and `pos.stop_loss_order_id` to `Position`.
  - `pos_by_client_id`: maps `pos.client_order_id` to `Position`.
  - `orders_by_ex_id`: maps active limit order exchange IDs to `Order`.
- State Machine:
  - Detection -> `FLAG` (recorded in `_flagged_orders[order_id]` with timestamp and retry counter).
  - Next cycle -> `VERIFY` (poll status, verify grace period / minimum consecutive mismatches).
  - Confirmed orphan -> `RECONCILE` (cancel or sync).

### `AutoTradeRouter.handle_signal` ↔ Caller
- Input: `payload: dict` or `OpportunitySignal` containing:
  - `verified_scanner_signal`: `True`
  - `risk_decision` or `is_risk_approved`: `True`
  - `trade_amount_inr`: capped at `min(amount, ORDER_SIZE_INR)`
- Rejection:
  - If unverified or not risk-approved -> return `{"success": False, "error": "UNAUTHORIZED_SIGNAL"}`.

## Code Layout
- `execution/trading/subaccount_manager.py` — Owned exclusively by Milestone M1 Worker
- `execution/reconciliation.py` — Owned exclusively by Milestone M2 Worker
- `execution/service.py` — Shared integration points (co-owned / updated in M2)
- `execution/auto_trader.py` — Owned exclusively by Milestone M3 Worker
- `tests/` — Test suites updated/verified in M4
