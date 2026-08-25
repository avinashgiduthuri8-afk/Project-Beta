---
name: TestClient anyio conflict
description: FastAPI TestClient with asyncio.to_thread inside route handlers produces 422 in pytest due to anyio plugin interference; direct handler invocation avoids this.
---

## The rule
Do NOT use `starlette.testclient.TestClient` to test FastAPI route handlers that internally call `asyncio.to_thread(...)` inside a pytest session that has the `anyio` plugin loaded.

**Why:** The anyio pytest plugin alters event-loop management at session level. Inside a `TestClient` context (which spawns its own thread), `asyncio.to_thread` calls in route handlers fail with a validation error that FastAPI converts to a spurious 422 Unprocessable Entity, even though `dependency_overrides` and the route code are correct. The failure is non-deterministic and invisible — the same code works fine when run directly via `anyio.run()` or as a subprocess, but consistently fails under `python -m pytest`.

**How to apply:**
1. For route handlers that use `asyncio.to_thread`, call the async handler function directly with a `MagicMock` request object instead of using `TestClient`.
2. Example pattern:
   ```python
   req = MagicMock()
   req.json = lambda: {"coin": "BTC", "base_price": 100.0}  # must be async if handler awaits it
   data = json.loads(asyncio.run(my_handler(req)).body)
   ```
3. For testing auth (which is an app-level dependency, not inside the handler), test the `require_api_key` function directly as a coroutine via `asyncio.run`.
4. For the `dependency_overrides` approach to work in pytest, the override must use `lambda: "value"` (no parameters) since FastAPI's DI with `Request` parameters also produces 422 under the anyio plugin.

**Also note:** Always return persisted state from write endpoints, not the pre-write normalized input — the storage layer may deduplicate/transform, so the response should call `get_*()` after the write to read back what was actually saved.
