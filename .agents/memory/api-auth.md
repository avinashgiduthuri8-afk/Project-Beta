---
name: API auth pattern
description: How PROJECT-ALPHA authenticates dashboard users and API callers.
---

# API Auth Pattern

## Session-based dashboard auth (browser)
- `/login` (GET/POST) and `/logout` are public; all other routes are protected.
- Browser users POST their API key to `/login`; on success `request.session["authenticated"] = True` is set via `SessionMiddleware` (itsdangerous, `SESSION_SECRET` env var required — raises `RuntimeError` if unset).
- `@app.get("/")` checks `request.session.get("authenticated")` and redirects to `/login` if false.
- `https_only` is conditional: `True` when `ENVIRONMENT=production`, `False` otherwise.
- `same_site="lax"` is set on the session cookie.

## X-API-Key protection (API calls)
- All `/api/*` routes require `X-API-Key` header validated by `require_api_key()` (app-level dependency).
- `require_api_key` uses `hmac.compare_digest()` — constant-time, prevents timing attacks.
- Fails closed: 503 if `DASHBOARD_API_KEY` env var is unset; 403 if key is wrong/missing.
- `_DASHBOARD_EXEMPT_PATHS` is now minimal: only `/health`, `/`, `/login`, `/logout`.
  - All `/api/*` paths (including watchlist, scanner) have been removed from the exempt list; they require X-API-Key.

## Frontend helper
- `authenticatedFetch(url, options)` in `script.js` adds `X-API-Key: window.DASHBOARD_API_KEY` to every fetch.
- Uses `new Headers(...)` + `headers.set(...)` to safely handle both plain-object and `Headers`-instance callers.
- `window.DASHBOARD_API_KEY` is injected via Jinja2 (`{{ dashboard_api_key | tojson }}`) — only reachable after session auth.

**Why:** Originally `/` was fully public with `dependencies=[]`, exposing `window.DASHBOARD_API_KEY` in page source. Session gate + minimal exempt list closes that bypass.

**How to apply:** When adding new browser-facing pages, apply the same session gate inside the route handler. When adding new API endpoints, do NOT add them to `_DASHBOARD_EXEMPT_PATHS` — `authenticatedFetch` already sends the key.
