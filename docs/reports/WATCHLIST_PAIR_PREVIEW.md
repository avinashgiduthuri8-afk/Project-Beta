# WATCHLIST PAIR PREVIEW — Implementation Report

**Date**: 2026-07-04  
**Task**: Real-time pair preview while typing a coin symbol  
**Type**: UI enhancement only — zero backend / scanner changes  
**Status**: ✅ Complete

---

## What Was Built

When the user types in the watchlist "Add coin" input, a live preview appears below it showing the resolved trading pair before they click "Add":

| Input | Preview | Color |
|---|---|---|
| `BTC` | `✓ BTC → BTC/INR` | Green |
| `ETH` | `✓ ETH → ETH/INR` | Green |
| `LINK` | `✓ LINK → LINK/INR` | Green |
| `PEPE` | `✓ PEPE → PEPE/USDT` | Amber |
| `FAKE` | `✗ No supported trading pair found` | Red |
| *(typing)* | `Resolving pair…` | Gray |
| *(empty)* | *(hidden)* | — |

---

## Files Changed

| File | Change |
|---|---|
| `dashboard/templates/dashboard.html` | Added `#scanner-pair-preview` div |
| `dashboard/static/script.js` | Added pair preview IIFE + clear-on-add |

**No backend changes. No scanner changes. No storage changes.**

---

## API Calls Made

Reuses the existing endpoint from Task 2:

```
GET /api/v1/scanner/resolve-pair/{coin}
```

The preview calls this endpoint (authenticated via `X-API-Key`) after a 300 ms debounce. All responses are cached in memory (last 20 lookups) so repeated lookups for the same coin never trigger a second request.

---

## Implementation Details

### `dashboard/templates/dashboard.html`

Added one line between the Add button and the error div:

```html
<div id="scanner-pair-preview"
     style="display:none;font-size:0.82rem;margin-top:5px;
            min-height:1.2em;font-weight:500;letter-spacing:0.01em;">
</div>
```

`min-height:1.2em` reserves the space so other elements don't shift when the preview appears/disappears.

---

### `dashboard/static/script.js`

Self-contained IIFE added at the end of the `DOMContentLoaded` block:

```
pairPreview IIFE
  ├── CACHE_MAX = 20         LRU eviction — oldest entry dropped when full
  ├── previewCache (Map)     coin → API result
  ├── debounceTimer          300 ms debounce handle
  ├── inflightCoin           tracks latest request; stale responses discarded
  ├── showPreview(state, text)   applies color + text + show/hide
  ├── renderResult(data)         green/amber/red based on resolved + quote
  └── resolvePreview(coin)       cache check → debounced fetch → render
```

**Debounce**: 300 ms after the last keystroke.

**Duplicate suppression**: `inflightCoin` is set before the fetch. If the user types faster than responses arrive, only the response matching the current `inflightCoin` renders.

**Cache**: `Map` with FIFO eviction at 20 entries (oldest key deleted first). Zero disk I/O.

**Clear on add**: `addCoin()` hides and empties the preview div on successful add.

---

## Visual States

| State | Trigger | Color | Text |
|---|---|---|---|
| **Hidden** | Empty input | — | *(none)* |
| **Loading** | Fetch in-flight | `#888888` gray | `Resolving pair…` |
| **INR pair** | `resolved=true, quote=INR` | `#22c55e` green | `✓ COIN → COIN/INR` |
| **USDT pair** | `resolved=true, quote=USDT` | `#f59e0b` amber | `✓ COIN → COIN/USDT` |
| **No pair** | `resolved=false` | `#ef4444` red | `✗ No supported trading pair found` |
| **Network error** | fetch throws | `#888888` gray | `Unable to resolve pair.` |

---

## Performance

| Requirement | Implementation |
|---|---|
| Debounce 300 ms | `setTimeout` cleared on every keystroke |
| No duplicate requests | Cache hit → no fetch; `inflightCoin` discards stale |
| Cache last 20 lookups | `Map` with size check + `keys().next().value` eviction |
| No polling | Event-driven only (`input` event) |
| Mobile layout | `min-height` prevents layout shift; `display:block` (not inline) wraps correctly |

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Network timeout / failure | Shows `Unable to resolve pair.` in gray; no console error |
| Stale response (user typed faster) | Discarded via `inflightCoin` check |
| Element not found (`#scanner-pair-preview` absent) | `showPreview` guards with `if (!el) return` |
| Watchlist add/remove | Unaffected — preview is purely display-only |

---

## Rollback Instructions

To remove this feature entirely:

1. **HTML** (`dashboard/templates/dashboard.html`): delete the `#scanner-pair-preview` div line
2. **JS** (`dashboard/static/script.js`):
   - Remove the `// ── Pair Preview … // ── End Pair Preview` IIFE block
   - Remove the 3-line preview clear in `addCoin()` (the `const preview = …` block)

No backend rollback required — the `/api/v1/scanner/resolve-pair/{coin}` endpoint is unchanged.

---

## Tests Run

- App restarted cleanly after changes: ✅
- Dashboard loads without JavaScript errors: ✅
- Watchlist add / remove still works: ✅
- Scanner unaffected: ✅
- `refreshWatchlistTable()` unchanged: ✅
- 0 regressions: ✅
