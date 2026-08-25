---
name: Snapshot cache pattern
description: How blocking snapshot I/O is offloaded and cached in app.py
---

`_SNAPSHOT_CACHE: dict[str, tuple[float, dict]]` with `_SNAPSHOT_TTL = 3.0` seconds.
`async def _cached_snapshot(key, fn)` checks the cache; on miss calls `await asyncio.to_thread(fn)`.

**Why:** `mtb_snapshot()`, `pmb_snapshot()`, `vgx_snapshot()`, `risk_snapshot()` all do file I/O and should not block the uvicorn event loop.

**How to apply:**
- Keys: `"vgx"`, `"mtb"`, `"pmb"`, `"risk"` — map to their respective `*_snapshot()` functions.
- Async routes use `await asyncio.gather(...)` for parallel fetches.
- `_unified_stats()` accepts optional `vgx/mtbs/pmbs` dicts so callers can pre-fetch and pass them in.
- `pull_state_payload()` is `async def` and uses `await _cached_snapshot(...)` throughout.
