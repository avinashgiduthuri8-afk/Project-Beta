# PROJECT-ALPHA V2 — Code Audit Report

## Summary

| Severity | Count | Theme |
| :--- | :--- | :--- |
| **Critical** | 6 | Secret exposure (VPS root & CoinDCX live keys), authentication bypass (dummy PIN/password check), unauthorized LIVE mode activation via Telegram & API, and uninitialized AI service runtime crash |
| **High** | 7 | Broken capital pool initialization (`trading_cfg` NameError), fake execution guard fallbacks (bypassing slippage/staleness & blocking orders), lack of multi-process signal polling in Live bot, test suite mutating repository config to LIVE, and incomplete Docker/compose configurations |
| **Medium** | 6 | Function redefinitions/shadowing, bare exception swallowing in trade accounting/PnL, API keys exposed in URL query parameters, WebSocket missing handshake authentication, and missing `reconcile_on_demand` method |
| **Low** | 4 | Duplicate dictionary literals, unused variables/imports, deprecated API aliases, and missing documentation for service ports |

> [!CAUTION]
> **Single Biggest Risk:**  
> The system has multiple pathways where unauthorized operators or unauthenticated requests can activate **LIVE trading with REAL MONEY** on CoinDCX without a valid password (via Telegram `/mode live confirm` which requires no password, or via `/api/v2/production/set-mode` which accepts any password except 3 hardcoded dummy strings), while plaintext VPS root credentials (`[REDACTED]`) and live CoinDCX API keys are present in repository scripts and environment configurations.

---

## Critical

### SEC-01 Plaintext VPS Root Credentials Committed in Repository Scripts
- **Where:** `scripts/check_port_and_kill.py:5`, `scripts/deploy_latest_vps.py:6`, `scripts/deploy_and_wipe_vps.py:6`, `scripts/restart_and_verify_vps.py:7`, `scripts/update_vps_unit.py:7`, `scripts/upload_to_vps.py:26`, `scripts/verify_vps.py:6`
- **Proof:**
  ```python
  # scripts/update_vps_unit.py:7
  client.connect("[REDACTED_IP]", port=20069, username="root", password="[REDACTED]")
  ```
  Git grep output:
  `scripts/update_vps_unit.py:7: client.connect("[REDACTED_IP]", port=20069, username="root", password="[REDACTED]")`
- **Impact:** Complete server compromise. Anyone with read access to the git repository has full root access to the live trading VPS (`[REDACTED_IP]:20069`).
- **Fix:** Immediately rotate the root password on the VPS. Remove all hardcoded credentials from scripts and load host, port, username, and SSH private keys strictly from secure environment variables or SSH agent.

### SEC-02 Dashboard API Key Exposed to Unauthenticated Web Clients
- **Where:** `app.py:480-481`, `dashboard/templates/dashboard.html:2348`
- **Proof:**
  ```python
  # app.py:477-482
  return templates.TemplateResponse(
      request=request,
      name="dashboard.html",
      context={
          "api_key": cfg.dashboard_api_key or "",
  ```
  ```javascript
  // dashboard/templates/dashboard.html:2348
  const API_KEY = "{{ api_key if api_key else '' }}";
  ```
- **Impact:** Unauthenticated route `GET /` serves HTML containing the server's master `DASHBOARD_API_KEY` in plaintext. Any visitor can read the API key and gain full authenticated access to all protected control endpoints.
- **Fix:** Never inject `cfg.dashboard_api_key` into template contexts or HTML responses. Implement a standard session/cookie authentication flow or require users to input their API key into the UI client-side.

### SEC-03 Authentication Bypass via Blacklist-Only Password Validation
- **Where:** `dashboard/api/router.py:236-250`, `dashboard/api/production_routes.py:184-190`
- **Proof:**
  ```python
  # dashboard/api/router.py:236-243
  @router.post("/auth/verify-password", tags=["auth"], dependencies=[Depends(require_api_key)])
  async def verify_dashboard_password(body: VerifyPasswordRequestSchema | None = None) -> dict:
      """Verify operator security PIN."""
      provided = (body.password if body else "") or ""
      if not provided or provided in ("wrong-pin", "wrong", "invalid"):
          raise HTTPException(
              status_code=status.HTTP_401_UNAUTHORIZED,
              detail="Invalid security PIN.",
          )
      return {"ok": True, "success": True, "valid": True, "authorized": True, "message": "Authenticated successfully."}
  ```
  ```python
  # dashboard/api/production_routes.py:184-190
  if target in ("LIVE_MICROCASH", "LIVE"):
      provided_pw = (body.password or "").strip()
      if not provided_pw or provided_pw in ("wrong-pin", "wrong", "invalid"):
          raise HTTPException(
              status_code=status.HTTP_403_FORBIDDEN,
              detail="Valid security password required to switch to LIVE mode.",
          )
  ```
- **Impact:** Any password (e.g., `"123"`, `"admin"`, `"password"`) is accepted as valid except literally `"wrong-pin"`, `"wrong"`, or `"invalid"`. Operator PIN/password validation does not check `cfg.dashboard_security_password` or any cryptographic hash.
- **Fix:** Replace blacklist checks with constant-time comparison (`hmac.compare_digest`) against `cfg.dashboard_security_password` or a salted bcrypt/argon2 hash.

### SEC-04 Live Mode Transition in Telegram Requires No Password or PIN
- **Where:** `telegram/telegram_interface.py:1049-1064`
- **Proof:**
  ```python
  # telegram/telegram_interface.py:1049-1064
  elif target in ("LIVE", "LIVE_MICROCASH"):
      if len(args) < 2 or args[1].lower() != "confirm":
          warn = (
              "⚠️ <b>CONFIRMATION REQUIRED</b>\n"
              "Switching to <b>LIVE</b> enables real money orders dispatched to CoinDCX.\n\n"
              "To proceed, send:\n"
              "<code>/mode live confirm</code>"
          )
          await self._telegram.send_message(text=warn, target_chat_id=str(chat_id))
          return
      self._config.deployment_mode = "LIVE_MICROCASH"
      self._config.trading_enabled = True
      self._config.shadow_mode = False
      msg = f"🔴 <b>Mode Switched to LIVE</b>\nReal money micro-orders enabled (₹{self._config.order_size_inr:.2f} notional)."
  ```
- **Impact:** Anyone with Telegram chat access can activate live money trading simply by typing `/mode live confirm`. No operator password or verification code is challenged.
- **Fix:** Require the operator security password as an argument (e.g. `/mode live <password>`) and verify it against `cfg.dashboard_security_password` before setting mode to LIVE.

### MON-01 Undefined Name `trading_cfg` Crashes Capital Pool Initialization
- **Where:** `execution/trading/subaccount_manager.py:1269-1279`
- **Proof:**
  ```python
  # execution/trading/subaccount_manager.py:1267-1280
  if self._config is None:
      if (
          "capital_pool" in trading_cfg
          or "total_portfolio_capital_inr" in data
      ):
          raw_pool = trading_cfg.get(
              "capital_pool", data.get("total_portfolio_capital_inr")
          )
          if raw_pool is not None:
              pool_limit = float(raw_pool)
      if "order_size_inr" in trading_cfg:
          order_size = float(trading_cfg["order_size_inr"])
  except Exception as e:
      logger.error("Failed to read %s: %s", self.config_path, e)
  ```
  `ruff check --select F821 execution/trading/subaccount_manager.py`:
  `execution\trading\subaccount_manager.py:1269:47: F821 Undefined name trading_cfg`
- **Impact:** When `self._config` is `None` (default in standalone initializations like `test_subaccount_manager_initialization`), evaluating `trading_cfg` raises a `NameError`. The error is swallowed by the `except Exception`, leaving `pool_limit = None`. As a result, `wallet_balance_inr` and `available_balance_inr` become `None`, causing subaccount sizing and balances to break.
- **Fix:** Use the loaded dictionary `data` instead of the non-existent variable `trading_cfg`.

### CRIT-01 Uninstantiated `_ai_service` Crashes `app.py` When Trading Enabled
- **Where:** `app.py:88, 157-160`
- **Proof:**
  ```python
  # app.py:88
  _ai_service: AIIntelligenceService | None = None
  ...
  # app.py:157-160
  if cfg.enable_app_trading:
      await _scanner_service.start()
      await _ai_service.start()
  ```
- **Impact:** `_ai_service` is declared as `None` at module level and is NEVER instantiated before line 159. If `ENABLE_APP_TRADING=true`, starting `app.py` crashes immediately with `AttributeError: 'NoneType' object has no attribute 'start'`.
- **Fix:** Instantiate `_ai_service = AIIntelligenceService(...)` before starting it, or guard with `if _ai_service is not None:`.

---

## High

### MON-02 Execution Guards Inject Fake Defaults and Block Orders
- **Where:** `execution/service.py:281-283`
- **Proof:**
  ```python
  # execution/service.py:280-283
  if is_live:
      ticker_price = float(payload.get("price") or order_data["entry_price"])
      data_time = payload.get("timestamp") or datetime.now(timezone.utc)
      volume_24h = float(payload.get("volume_24h") or 100000.0)
  ```
- **Impact:**
  1. `ticker_price` defaulting to `order_data["entry_price"]` defeats the slippage check (0% slippage measured when price is absent).
  2. `data_time` defaulting to `datetime.now(timezone.utc)` defeats the stale market data check.
  3. `volume_24h` defaulting to `100000.0` is lower than the default required threshold `200000.0`, causing orders with omitted volume in test/live payloads to be rejected with `"Insufficient 24h liquidity for SOL: ₹100,000.00 < minimum ₹200,000.00"` (this was the root cause of 9 failing live execution tests).
- **Fix:** Fetch fresh live market data directly from the exchange/feed before executing orders. If volume, price, or timestamp is missing, query the ticker instead of injecting arbitrary fallback constants.

### MON-03 Multi-Process Live Bot Lacks Scanner Signal Polling Loop
- **Where:** `live/main.py:101-112`
- **Proof:**
  ```python
  # live/main.py:101-112
  bus.subscribe(EventType.SIGNAL_GENERATED, live_router.handle_signal_event)
  logger.info(
      "Alpha-Live service RUNNING (Trading Enabled: %s, DB: %s).",
      l_cfg.trading_enabled,
      l_cfg.db_path,
  )
  try:
      while True:
          await asyncio.sleep(1)
  ```
- **Impact:** In the 4-process architecture, Scanner runs in a separate OS process and cannot publish events across in-memory `bus` instances. While `paper/main.py` implements an explicit `poll_scanner_signals()` loop against `scanner/data/alpha_scanner.db`, `live/main.py` lacks any database signal polling loop. As a result, `live/main.py` never ingests or executes scanner signals.
- **Fix:** Implement a background polling loop `poll_scanner_signals()` in `live/main.py` that queries `scanner_signal_repo.get_live()` periodically, identical to `paper/main.py`.

### MON-04 Test Suite Writes to Real Repository Files and Leaves System in Live Mode
- **Where:** `tests/test_min_order_size_invariant.py:81-83`, `data/config_override.json`
- **Proof:**
  Running `pytest tests -q` resulted in:
  `git status --porcelain -> M data/config_override.json`
  `git diff data/config_override.json`:
  ```diff
  -  "deployment_mode": "PAPER",
  +  "deployment_mode": "LIVE_MICROCASH",
  ```
- **Impact:** Running the test suite alters the production configuration file on disk to `LIVE_MICROCASH` and `trading_enabled=true`. If the bot service is subsequently restarted, it will run in live mode with real trading enabled.
- **Fix:** In tests, mock `AppConfig.save_runtime_overrides` or use a temporary test directory (`tmp_path`) for configuration overrides instead of writing to `data/config_override.json`.

### DEP-01 Dockerfile Omissions and Broken Health Check
- **Where:** `Dockerfile:22-29, 36-37`
- **Proof:**
  ```dockerfile
  # Dockerfile:22-29
  COPY core/ ./core/
  COPY scanner/ ./scanner/
  COPY execution/ ./execution/
  COPY background/ ./background/
  COPY telegram/ ./telegram/
  COPY dashboard/ ./dashboard/
  COPY data/ ./data/
  COPY app.py .
  # Dockerfile:36-37
  HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
      CMD curl -f http://localhost:${PORT}/health || exit 1
  ```
- **Impact:**
  1. `paper/`, `live/`, `shared/`, and `adapters/` are not copied into the Docker container. Any imports from these modules will fail with `ModuleNotFoundError`.
  2. The healthcheck queries `http://localhost:${PORT}/health`, but `app.py` mounts the health route at `/api/health` and `/api/v2/health`. A request to `/health` returns HTTP 404, causing the container to be perpetually marked `unhealthy`.
- **Fix:** Add `COPY paper/ ./paper/`, `COPY live/ ./live/`, `COPY shared/ ./shared/`, and `COPY adapters/ ./adapters/`. Update health check target to `http://localhost:${PORT}/api/v2/health`.

### DEP-02 Docker Compose Syntax Error and Non-Existent Mount
- **Where:** `docker-compose.yml:11, 16, 18`
- **Proof:**
  ```yaml
  ports:
    - ":5001"
  volumes:
    - alpha_v2_data:/app/v2/data
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:5001/health"]
  ```
- **Impact:**
  1. `ports: - ":5001"` is invalid YAML syntax in many Docker Compose versions (missing host port mapping `5001:5001`).
  2. Volume `alpha_v2_data` is mounted to `/app/v2/data`, but `app.py` writes SQLite data to `/app/data/project_alpha.db`. Persistent database files are not saved to the mounted volume.
  3. Only 1 container is defined; services 1, 2, and 3 (`scanner`, `paper`, `live`) are completely absent from Docker Compose.
- **Fix:** Fix port mapping to `"5001:5001"`, correct volume mount to `/app/data`, update health check endpoint to `/api/v2/health`, and define services for scanner, paper, and live bots.

### DEP-03 Systemd Unit Only Runs Dashboard Service
- **Where:** `scripts/update_vps_unit.py:9-26`
- **Proof:**
  ```ini
  [Service]
  Type=simple
  User=root
  WorkingDirectory=/opt/project-alpha
  Environment=PATH=/opt/project-alpha/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
  ExecStart=/opt/project-alpha/.venv/bin/python3 app.py
  Restart=always
  ```
- **Impact:** The systemd configuration deployed to the VPS only runs `app.py`. Since `app.py` defaults to `ENABLE_APP_TRADING=false` (monitoring-only mode), none of the trading, scanning, or execution services are executed under systemd.
- **Fix:** Create separate systemd service units for `project-alpha-scanner.service`, `project-alpha-paper.service`, `project-alpha-live.service`, and `project-alpha-dashboard.service`.

### MON-05 Dual-Layer Stop-Loss Silent Failure Handling
- **Where:** `execution/service.py:603-622`
- **Proof:**
  ```python
  # execution/service.py:603-622
  else:
      sl_err = sl_res.get("message") or sl_res.get("error") or "Failed to place disaster stop-loss"
      logger.critical(
          "[%s] CRITICAL: Failed to place resting disaster stop-loss for %s (pos_id=%s): %s",
          sub_client.subaccount_id, pos.coin, pos.id, sl_err,
      )
      await self._bus.publish(EventType.ALERT_GENERATED, {...})
  ```
- **Impact:** If placing a resting stop-loss on CoinDCX fails after a BUY order fills, the position remains open in the market without an active exchange-side disaster stop loss. The error only emits an in-memory alert event without aborting or triggering an emergency market exit.
- **Fix:** When resting stop-loss placement fails, trigger an immediate emergency exit or retry loop to prevent unhedged market exposure.

---

## Medium

### CLN-01 Redefinition of Functions in Scope (F811)
- **Where:**
  - `execution/service.py:1033` and `1417` (`reconcile_live_orders` defined twice)
  - `execution/trading/subaccount_manager.py:216`, `493`, and `1092` (`cancel_order` defined 3 times)
  - `execution/trading/subaccount_manager.py:503` and `860` (`get_order_status` defined twice)
  - `core/repository/position_repo.py:180` and `214` (`close_position` defined twice)
  - `dashboard/bot_pipeline.py:307` and `518` (`sync_from_repository` defined twice)
  - `dashboard/service.py:260` and `427` (`get_telemetry_snapshot` defined twice)
  - `telegram/formatters.py:300` and `1111` (`format_telegram_portfolio` defined twice)
- **Proof:**
  `ruff check --select F811 --exclude tests .`:
  `execution\service.py:1417:15: F811 Redefinition of unused reconcile_live_orders from line 1033`
  `execution\trading\subaccount_manager.py:493:15: F811 Redefinition of unused cancel_order from line 216`
  `execution\trading\subaccount_manager.py:860:15: F811 Redefinition of unused get_order_status from line 503`
  `execution\trading\subaccount_manager.py:1092:15: F811 Redefinition of unused cancel_order from line 493`
- **Impact:** Earlier implementations are shadowed and discarded at import time. Developers expecting earlier signatures or behaviors experience unexpected side-effects.
- **Fix:** Consolidate redundant function definitions and remove stale duplicates.

### CLN-02 Bare `try-except-pass` Blocks in Money and State Code (S110)
- **Where:**
  - `execution/service.py:935-936`
  - `execution/service.py:1324-1325`
  - `execution/trading/subaccount_manager.py:776-777`
- **Proof:**
  ```python
  # execution/service.py:935-936
  except Exception:
      pass
  ```
  ```python
  # execution/trading/subaccount_manager.py:776-777
  except Exception:
      pass
  ```
- **Impact:** Failures in trade accounting, realized PnL calculations, and order status parsing fail silently, corrupting historical analytics and audit logs.
- **Fix:** Replace `pass` with explicit error logging and re-raise or handle domain-specific exceptions.

### SEC-05 API Keys Accepted in URL Query Parameters
- **Where:** `dashboard/api/auth.py:20, 48`, `dashboard/api/websocket.py:41`
- **Proof:**
  ```python
  # dashboard/api/auth.py:18-20
  async def require_api_key(
      x_api_key: str | None = Header(default=None, alias="X-API-Key"),
      api_key: str | None = Query(default=None, alias="api_key"),
  ) -> None:
      ...
      provided = x_api_key or api_key
  ```
- **Impact:** API keys passed in URL query strings (`?api_key=...`) are stored in plaintext across reverse proxy access logs, browser history, and HTTP `Referer` headers.
- **Fix:** Accept API keys exclusively via the `X-API-Key` or `Authorization: Bearer` headers.

### SEC-06 WebSocket Route Lacks Handshake Authentication Dependency
- **Where:** `dashboard/api/websocket.py:41-60`, `app.py:444`
- **Proof:**
  FastAPI route registration: `set() /ws/feed -> websocket_feed (deps: 0)`
  Inside `websocket_feed`: if `cfg.dashboard_api_key` is empty or None, connection behavior falls back or throws generic internal errors rather than failing closed at the router gateway level.
- **Impact:** Unauthenticated clients can open persistent WebSocket connections, consuming socket descriptors and memory.
- **Fix:** Attach authentication verification at connection initialization and reject handshake immediately before upgrading HTTP connection.

### MON-06 Missing `reconcile_on_demand` Method on `ReconciliationService`
- **Where:** `tests/test_reconciliation_on_demand.py:53, 90, 126`
- **Proof:**
  `AttributeError: 'ReconciliationService' object has no attribute 'reconcile_on_demand'`
- **Impact:** Code attempting on-demand manual reconciliation against CoinDCX fails with an `AttributeError`.
- **Fix:** Expose a public `reconcile_on_demand()` method on `ReconciliationService` that delegates to `reconcile_positions()`.

### MON-07 Undefined Name `Any` in Type Annotations (F821)
- **Where:**
  `execution/position_manager.py:55:29`, `execution/risk/circuit_breaker.py:80:20`, `execution/risk/service.py:80:52`, `execution/service.py:109:53, 1033:56, 1417:56`, `execution/trading/precision_rules.py:263:32, 280:28`
- **Proof:**
  `ruff check --select F821 execution/`:
  `execution\position_manager.py:55:29: F821 Undefined name Any`
- **Impact:** Calling functions or inspecting types at runtime when `from __future__ import annotations` is omitted raises `NameError: name 'Any' is not defined`.
- **Fix:** Add `from typing import Any` to imports in affected modules.

---

## Low

### CLN-03 Repeated Dictionary Key Literals (F601)
- **Where:** `background/production/controller.py:265-267`, `scanner/research/service.py:245`
- **Proof:**
  ```python
  # scanner/research/service.py:244-245
  "indicators": {
      "15m": ind_15m,
      "15m": ind_15m,
      "1d": ind_1d,
  }
  ```
- **Impact:** Dead code in dictionary definitions. First key is silently overwritten by duplicate key.
- **Fix:** Remove repeated dictionary keys.

### CLN-04 Hardcoded Fallback API Key `'alpha-prod-key'` in Frontend JS
- **Where:** `dashboard/static/js/dashboard.js:12`
- **Proof:**
  ```javascript
  this.apiKey = urlParams.get('api_key') || serverKey || localStorage.getItem('api_key') || localStorage.getItem('alpha_api_key') || 'alpha-prod-key';
  ```
- **Impact:** Browser automatically defaults to `'alpha-prod-key'` if unconfigured, making operators believe auth is configured when a hardcoded string is being submitted.
- **Fix:** Remove `'alpha-prod-key'` fallback and prompt user for API key in the UI.

### CLN-05 Deprecated Aliases and Legacy Endpoints
- **Where:** `app.py:443` (`/api/v2` duplicated with `/api`), `dashboard/api/router.py:26`
- **Proof:** Both `/api` and `/api/v2` routers mount the exact same endpoints.
- **Impact:** Route confusion and potential divergence during refactoring.
- **Fix:** Standardize on `/api/v2` and deprecate `/api`.

### CLN-06 Missing Port and Endpoint Documentation in Setup Guides
- **Where:** `RUNBOOK.md`, `README.md`
- **Proof:** Documentation references port 8080 or 5000 in several places, while `app.py` binds to port 5001.
- **Impact:** Operators running external monitors or health probes hit incorrect ports.
- **Fix:** Standardize documentation to port 5001.

---

## Test results

- **Passed:** 858
- **Failed:** 17
- **Warnings:** 1216
- **Duration:** 255.29s (0:04:15)

### Failing Tests Breakdown

| Failing test | Root cause | Related finding ID |
| :--- | :--- | :--- |
| `tests/test_execution_master.py::test_03_live_buy_calls_place_live_order[asyncio]` | `execution/service.py:283` injects fake `volume_24h = 100000.0` fallback, which fails execution guard requirement (`min_24h_volume = 200000.0`), rejecting order before `place_live_order` | MON-02 |
| `tests/test_execution_master.py::test_06_07_08_filled_buy_persists_order_id_and_actual_qty[asyncio]` | Fake `volume_24h` default (100k < 200k) blocks live order execution | MON-02 |
| `tests/test_execution_master.py::test_16_timeout_does_not_blindly_duplicate_buy[asyncio]` | Fake `volume_24h` default (100k < 200k) blocks live order execution | MON-02 |
| `tests/test_protective_exits.py::test_disaster_stop_limit_created_on_buy_fill[asyncio]` | Fake `volume_24h` default (100k < 200k) blocks live order execution, preventing stop-loss order trigger | MON-02 |
| `tests/test_protective_exits.py::test_disaster_stop_limit_failure_emits_alert[asyncio]` | Fake `volume_24h` default (100k < 200k) blocks live order execution | MON-02 |
| `tests/test_real_execution_wiring.py::test_2_and_6_live_buy_confirmed_filled[asyncio]` | Fake `volume_24h` default (100k < 200k) blocks live order execution | MON-02 |
| `tests/test_real_execution_wiring.py::test_12_live_mode_never_calls_paper_place_order[asyncio]` | Fake `volume_24h` default (100k < 200k) blocks live order execution | MON-02 |
| `tests/e2e/test_e2e_paper_live_isolation.py::test_complete_paper_live_isolation_workflow` | Fake `volume_24h` default (100k < 200k) blocks live trade execution in live leg of workflow | MON-02 |
| `tests/e2e/test_paper_live_isolation.py::test_complete_paper_live_isolation_workflow` | Fake `volume_24h` default (100k < 200k) blocks live trade execution | MON-02 |
| `tests/test_bot_pipeline.py::TestCoinDCXSubAccountArchitecture::test_subaccount_manager_initialization` | Undefined name `trading_cfg` in `subaccount_manager.py:1269` raises `NameError`, causing `wallet_balance_inr` to remain `None` instead of `10000.0` | MON-01 |
| `tests/test_bot_pipeline.py::TestCoinDCXSubAccountArchitecture::test_subaccount_order_placement_and_balance_isolation` | `wallet_balance_inr` and `available_balance_inr` are `None` due to `trading_cfg` `NameError` | MON-01 |
| `tests/test_reconciliation_on_demand.py::test_reconcile_on_demand_clean_state` | `ReconciliationService` does not have method `reconcile_on_demand` | MON-06 |
| `tests/test_reconciliation_on_demand.py::test_reconcile_on_demand_orphan_order_detected` | `ReconciliationService` does not have method `reconcile_on_demand` | MON-06 |
| `tests/test_reconciliation_on_demand.py::test_reconcile_on_demand_balance_mismatch_detected` | `ReconciliationService` does not have method `reconcile_on_demand` | MON-06 |
| `tests/test_reconciliation_real_coindcx.py::test_reconciliation_clean_state[asyncio]` | Reconciliation detects mismatch with mock CoinDCX positions (`DISCREPANCIES_DETECTED != IN_SYNC`) | MON-06 |
| `tests/test_correlation_exposure_limits.py::test_capital_guard_correlation_integration` | `CapitalGuard.__init__() got an unexpected keyword argument 'correlation_guard'` | CLN-05 |
| `tests/test_adversarial_robustness.py::test_evaluate_signal_robustness_elite_and_chase` | Nominal risk-reward assertion failed (`3.125 >= 4.5`) due to tightened strategy calculation bounds | CLN-05 |

---

## Cross-check with ISSUES.md

| ID | Status | Proof (file:line or command output) |
| :--- | :--- | :--- |
| **SEC-1** | CONFIRMED | `app.py:467-470` unauthenticated `GET /`, `/dashboard`, `/v2/dashboard` routes |
| **SEC-2** | CONFIRMED | `app.py:481` passes `cfg.dashboard_api_key` to Jinja context; `dashboard.html:2348` renders `const API_KEY = "{{ api_key }}";` |
| **SEC-3** | CONFIRMED | `dashboard/api/router.py:239` & `production_routes.py:186` only blacklists `("wrong-pin", "wrong", "invalid")` |
| **SEC-4** | CONFIRMED | `telegram/telegram_interface.py:1050-1064` switches to `LIVE_MICROCASH` via `/mode live confirm` without password |
| **SEC-5** | CONFIRMED | `telegram/telegram_interface.py:252-278` validates only `chat_id` against whitelist, no command-level RBAC or 2FA |
| **SEC-6** | CONFIRMED | `scripts/update_vps_unit.py:7` contains root password `"[REDACTED]"`; `.env` contains live keys `"af85..."` |
| **SEC-7** | CONFIRMED | `dashboard/api/auth.py:20, 48` accepts `api_key: str | None = Query(default=None, alias="api_key")` |
| **SEC-8** | CONFIRMED | `dashboard/api/websocket.py:41` allows WebSocket handshake without router dependency enforcement |
| **MON-1** | CONFIRMED | `execution/trading/subaccount_manager.py:1269` undefined name `trading_cfg` crashes capital initialization |
| **MON-2** | CONFIRMED | `core/config.py:1031` clamps `order_size_inr` to 200.0, but `execution/service.py:317` does not assert >= 200.0 before live call |
| **MON-3** | CONFIRMED | `execution/service.py:603-615` disaster stop-loss placement failure only logs critical and publishes event without halting trade |
| **MON-4** | CONFIRMED | `execution/service.py:281-283` fake defaults (`order_data["entry_price"]`, `datetime.now()`, `100000.0`) |
| **MON-5** | CONFIRMED | `live/main.py:101-112` has no scanner DB polling loop; cannot receive signals in multi-process deployment |
| **MON-6** | CONFIRMED | `execution/service.py:1033` vs `1417` duplicate definitions; `ReconciliationService` missing `reconcile_on_demand` |
| **MON-7** | CONFIRMED | `execution/service.py:263` active coin check rejects same coin even when in-trade scaling/pyramiding is valid |
| **MON-8** | CONFIRMED | `execution/service.py:447` redundant client_order_id fallback logic during timeout ambiguous order resolution |
| **CLN-1** | CONFIRMED | `ruff check --select F811 .` found 9 duplicate function definitions across core, execution, dashboard, and telegram |
| **CLN-2** | CONFIRMED | `ruff check --select F821 .` found 13 undefined names (`trading_cfg`, `Any`, `Optional`, and `_ai_service`) |
| **CLN-3** | CONFIRMED | `ruff check --select F601 .` found 4 duplicate dict keys in `controller.py:265-267` and `scanner/research/service.py:245` |
| **CLN-4** | CONFIRMED | `ruff check --select S110 execution/` found 3 bare `except: pass` in trade persistence and order status |
| **CLN-5** | CONFIRMED | `pytest tests -q` failed with 17 test failures out of 875 |
| **CLN-6** | CONFIRMED | `pytest` run dirtied `data/config_override.json` to `"deployment_mode": "LIVE_MICROCASH"` |
| **CLN-7** | CONFIRMED | `Dockerfile`, `docker-compose.yml`, and `scripts/update_vps_unit.py` missing services, volumes, and modules |

### New Issues Found Not in ISSUES.md

1. **NEW-01: `app.py` Crash on Startup with Trading Enabled**  
   `app.py:159` calls `await _ai_service.start()` when `ENABLE_APP_TRADING=true`, but `_ai_service` is never instantiated (`None`).
2. **NEW-02: SQLite Database Path Mismatch Across Services**  
   `app.py` uses `data/project_alpha.db`, `paper/main.py` uses `paper/data/alpha_paper.db`, `live/main.py` uses `live/data/alpha_live.db`, and `scanner/main.py` uses `scanner/data/alpha_scanner.db`. The dashboard API never reads open paper/live positions because it looks only at `data/project_alpha.db`.
3. **NEW-03: Dependency Vulnerabilities via `pip-audit`**  
   135 vulnerabilities across 11 dependencies (`curl-cffi`, `gitpython`, `idna`, `pillow`, `pip`, `requests`, `soupsieve`, `starlette`, `tornado`, `urllib3`, `werkzeug`).
4. **NEW-04: Hardcoded Fallback Credentials in Frontend JavaScript**  
   `dashboard/static/js/dashboard.js:12` falls back to `'alpha-prod-key'` when no key is found in localStorage or query params.

---

## Progress estimate

| Service | % Done | Evidence |
| :--- | :--- | :--- |
| **Scanner** (`scanner/main.py`) | **85%** | Standalone runner works, Top-50 liquidity scanner, multi-timeframe analysis and signal generation functional; minor duplicate dict key in research service. |
| **Paper Bot** (`paper/main.py`) | **80%** | Autonomous mark-to-market exit monitor, QPB portfolio allocation, and paper execution router work; isolated DB writes functional; 2 E2E isolation tests fail due to shared execution guard defaults. |
| **Live Bot** (`live/main.py`) | **45%** | Missing signal polling bridge from Scanner DB; in-memory bus is isolated per process; stop-loss failure lacks defensive unwind; live execution blocked in tests by volume default. |
| **Dashboard** (`app.py` + `dashboard/`) | **60%** | Complete UI templates, WebSocket feed, and REST routes implemented; lacks proper auth (API key leaks in HTML, password bypass); DB desync with Paper/Live bots; startup crash when trading enabled. |
| **Security / Deploy** | **35%** | Plaintext VPS root credentials committed to git; `.env` with live exchange keys; broken Dockerfile & docker-compose; systemd units only run 1 of 4 services; tests mutate production config. |
