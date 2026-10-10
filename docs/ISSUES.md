# ISSUES.md — PROJECT-ALPHA V2 Code-Complete Tracker

RULES FOR THIS FILE
- This is the ONLY list of work. Nothing outside this list gets changed.
- Only the OWNER (human) changes a status to DONE, after reviewing the diff and the test output.
- The AI may only write "READY FOR REVIEW" next to an issue.
- Line numbers are from commit 532e031 and may shift slightly.

Status values: TODO / READY FOR REVIEW / DONE

---

## PHASE 0 — Owner does this by hand (no AI)

| ID | Task | Status |
|---|---|---|
| P0-1 | Change VPS root password, disable SSH password login, create non-root deploy user with SSH key | TODO |
| P0-2 | Revoke Telegram bot token in @BotFather and create a new one | TODO |
| P0-3 | Rotate DASHBOARD_API_KEY (32+ random chars), SESSION_SECRET, CoinDCX API key/secret | TODO |
| P0-4 | Make the GitHub repo private | TODO |
| P0-5 | On the VPS set DEPLOYMENT_MODE=PAPER in .env and restart all services | TODO |

---

## PHASE 1 — Security

### SEC-1 Hardcoded VPS root password in code — TODO
- Where: `scripts/upload_to_vps.py:26`, every file in `scratch/` that calls `ssh.connect`, scripts in `.agents/`.
- Fix: delete `scratch/` and `.agents/` from the repo. In `scripts/upload_to_vps.py` read `VPS_HOST`, `VPS_PORT`, `VPS_USER` from env, authenticate with an SSH key (no password), use `paramiko.RejectPolicy()` + `load_system_host_keys()`.
- Done when: `git grep -n "148.113.9.103"` and `git grep -n "SMT6"` return nothing.

### SEC-2 Dashboard page leaks the API key — TODO
- Where: `app.py:467-484` (`/`, `/dashboard`, `/v2/dashboard` render `api_key` into HTML with no auth). Template: `dashboard/templates/dashboard.html` + `dashboard/static/js/dashboard.js` (both reference `api_key`).
- Fix: add `GET /login` + `POST /login` (form with the API key). On success set a signed session cookie (`HttpOnly`, `SameSite=Strict`, `Secure` when HTTPS). Dashboard routes redirect to `/login` without a valid cookie. Remove `api_key` from the template context and from the JS. JS calls use same-origin cookies.
- Done when: `GET /` without cookie → 302 to `/login`; response body never contains the API key.

### SEC-3 LIVE-mode PIN accepts any value — TODO
- Where: `dashboard/api/production_routes.py:184-190`, `dashboard/api/router.py:236-249`.
- Current: only rejects `"wrong-pin"`, `"wrong"`, `"invalid"`.
- Fix: `hmac.compare_digest(provided, cfg.dashboard_security_password)`. If `dashboard_security_password` is unset → refuse LIVE (403). Add rate limit (5 failed tries / 15 min per IP). Log every mode change with IP + timestamp.
- Done when: wrong PIN → 403, correct PIN → 200, 6th wrong try → 429.

### SEC-4 API key accepted in URL query string — TODO
- Where: `dashboard/api/auth.py:20` (`?api_key=`), `dashboard/api/websocket.py:41`.
- Fix: header `X-API-Key` or session cookie only. WebSocket authenticates via the session cookie.
- Done when: `?api_key=<valid>` alone → 401.

### SEC-5 Weak API keys allowed — TODO
- Where: `dashboard/api/auth.py:38-41` (denylist only applies in LIVE mode).
- Fix: at app startup refuse to start if `DASHBOARD_API_KEY` < 32 chars, in ALL modes.

### SEC-6 Telegram control commands authorized by chat only — TODO
- Where: `telegram/telegram_interface.py:252-278` (`is_authorized`), `:346-356` (messages), `:490` (callbacks).
- Fix: new config `TELEGRAM_ADMIN_USER_IDS`. For `/mode`, `/kill`, `/resume`, `/close` and their buttons, require `message.from.id` in that list. `/mode live` additionally requires the PIN (same check as SEC-3).

### SEC-7 Server exposed on all interfaces — TODO
- Where: `core/config.py:716` (`host="0.0.0.0"`), `docker-compose.yml` (`":5001"`).
- Fix: default host `127.0.0.1`; compose `127.0.0.1:5001:5001`.

### SEC-8 Vulnerable Starlette — TODO
- Where: `requirements.txt` (fastapi 0.116.1 → starlette 0.47.3, 9 advisories).
- Fix: upgrade fastapi to a version whose starlette has no known advisories; `pip-audit` clean.

---

## PHASE 2 — Money safety (real-money path)

### MON-1 Capital pool is infinite — TODO
- Where: `execution/trading/subaccount_manager.py:1260-1280`. `trading_cfg` is never defined → `NameError` swallowed by `except Exception` → `pool_limit` stays `math.inf`.
- Fix: `trading_cfg = data.get("trading", {})`. Narrow the except to `(OSError, json.JSONDecodeError)`. If pool limit still unresolved → raise / refuse to trade. Never default to `math.inf`.
- Fixes tests: `test_bot_pipeline.py::...test_subaccount_manager_initialization`, `...test_subaccount_order_placement_and_balance_isolation`.

### MON-2 Execution guards are fed fake market data — TODO (blocks 7+ tests)
- Where: `execution/service.py:281-283`
  ```python
  ticker_price = float(payload.get("price") or order_data["entry_price"])  # slippage guard can never fail
  data_time   = payload.get("timestamp") or datetime.now(timezone.utc)   # stale guard can never fail
  volume_24h  = float(payload.get("volume_24h") or 100000.0)             # always < 200000 min → every live order blocked
  ```
- Fix: guards must use REAL data. If `price` / `timestamp` / `volume_24h` missing in payload, fetch from the market feed / ticker cache. If still unavailable → reject the order with reason `MISSING_MARKET_DATA`. No invented defaults.
- Tests: update test fixtures to send realistic `price`, `timestamp`, `volume_24h` (>= `scanner_min_24h_volume`). DO NOT lower the 200000 threshold to make tests pass.
- Fixes tests: `test_execution_master.py` 03, 05, 06_07_08, 16; `test_real_execution_wiring.py` 2_and_6, 12; `test_protective_exits.py` (2); likely both `tests/e2e/*paper_live_isolation*`.

### MON-3 Committed runtime config forces LIVE — TODO
- Where: `data/config_override.json` (contains `"v2_deployment_mode": "LIVE_MICROCASH"`).
- Fix: `git rm --cached data/config_override.json`, add to `.gitignore`, ship `config/config_override.example.json` with PAPER and `order_size_inr: 200`. Environment variable `DEPLOYMENT_MODE` must take precedence over any JSON file. Missing → PAPER.

### MON-4 Test suite overwrites real config — TODO
- Proof: after `pytest tests`, `data/config_override.json` becomes `LIVE_MICROCASH` + `order_size_inr: 750`.
- Fix: add to the existing `tests/conftest.py` an autouse fixture: redirect config override path, every DB path and log path to `tmp_path`; clear `get_config` lru_cache per test. Add `testpaths = tests` to `pytest.ini`.
- Done when: `pytest tests && git status --porcelain` prints nothing.

### MON-5 Duplicate / conflicting mode keys — TODO
- Where: `background/production/controller.py:258-267` (v2_* keys written twice in one dict; second set wins). Same dual-key pattern in `production_routes.py`, `core/config.py`.
- Fix: one canonical set: `deployment_mode`, `trading_enabled`, `shadow_mode`. Read legacy `v2_*` once for backward compatibility, never write them.

### MON-6 Reconciliation incomplete — TODO
- `ReconciliationService.reconcile_on_demand()` does not exist (`execution/reconciliation.py`) → 3 tests in `tests/test_reconciliation_on_demand.py` fail. Implement it as a single synchronous pass of `reconcile_positions()` returning the report (no background loop).
- `tests/test_reconciliation_real_coindcx.py::test_reconciliation_clean_state` returns `DISCREPANCIES_DETECTED` for zero positions and ₹10,000 balance. Find which list (`balance_mismatches` / `position_mismatches`, around lines 425-472) is non-empty and why. Likely linked to MON-1 (infinite wallet). Fix the cause, not the test.

### MON-7 Correlation exposure guard not wired — TODO
- `execution/risk/correlation_limits.py::CorrelationExposureGuard` exists but `execution/risk/capital_guard.py:20` `CapitalGuard.__init__` has no `correlation_guard` param.
- Fix: accept optional `correlation_guard`, call `check_exposure` inside `check_trade`, wire it in `execution/risk/service.py`.
- Fixes test: `tests/test_correlation_exposure_limits.py::test_capital_guard_correlation_integration`.

### MON-8 Paper/live isolation — TODO
- `tests/e2e/test_paper_live_isolation.py` and `tests/e2e/test_e2e_paper_live_isolation.py` fail (`mock_place_order.call_count == 0`). Re-run after MON-2. If still failing, fix the isolation, not the test. Also: the two files look like duplicates — keep one.

---

## PHASE 3 — Code health

### CLN-1 Shadowed duplicate functions (only the LAST copy runs) — TODO
Keep ONE correct version, delete the rest, re-run tests:
- `execution/trading/subaccount_manager.py`: `cancel_order` at 216, 493, 1092; `get_order_status` at 503, 860. Note 493 uses path `exchange/v1/...` without leading slash.
- `execution/service.py`: `reconcile_live_orders` at 1033 and 1417.
- `core/repository/position_repo.py`: `close_position` alias at 180 and def at 214.
- `dashboard/service.py`: `get_telemetry_snapshot` at 260 and 427.
- `dashboard/bot_pipeline.py`: `sync_from_repository` at 307 and 518.
- `dashboard/api/router.py:24-26`: `production_router` imported twice.
- Done when: `ruff check --select F811 .` (excluding tests) = 0.

### CLN-2 Scanner indicators computed twice — TODO
- `scanner/research/service.py:215-216` computes `ind_15m` twice; `:244-245` has `"15m"` key twice. Second one was probably meant to be `1h`. Owner confirms (see DECISIONS D5).

### CLN-3 Silent exception swallowing in money code — TODO
- `ruff check --select S110,S112 execution background/production core` — replace `except: pass` with logging + fail-closed behavior.

### CLN-4 Lint gate — TODO
- `ruff check --select F811,F821,F601,F841,F401 core scanner execution background telegram dashboard paper live shared` = 0.

### CLN-5 Requirements — TODO
- `requirements.txt` lists every package twice and leaves most unpinned. Deduplicate, pin, generate a lockfile. Add `paramiko` only to a separate `requirements-dev.txt` if deploy scripts stay.

### CLN-6 Docker — TODO
- `docker-compose.yml` mounts `/app/v2/data` but app writes `/app/data` → data lost on rebuild. Mount `/app/data`.
- `Dockerfile` does not copy `paper/`, `live/`, `shared/`, so the paper and live bots cannot run from the image. Copy them, or one image with 4 commands.

### CLN-7 Docs reflect reality — TODO
- `project_status.md` still describes `v2/app_v2.py` and 4 bots; `PROJECT.md` marks M1–M4 DONE while their bugs exist. Replace with ONE `STATUS.md` that lists only what the tests prove. AI may not write "DONE" anywhere.

---

## PHASE 4 — Optional, only after Phases 1–3 are DONE
- RST-1: 5-folder restructure (`src/`, `tests/`, `deploy/`, `config/`, `docs/`) as a separate branch, moves only, no logic changes.
