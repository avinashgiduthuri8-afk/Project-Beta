---
name: Scanner history lock upgrade
description: _history_lock in scanner.py must be RLock; append_signal_history() holds it across the full read+write
---

## Rule
`bots/scanner_bot/scanner.py _history_lock` must be `threading.RLock()`, not `threading.Lock()`.
`append_signal_history()` must hold `_history_lock` across the entire read→dedup→append→write sequence.

**Why:**
- Pre-fix: `_read_history()` was called outside `_history_lock`; two concurrent callers both read the same snapshot, both passed dedup, and the second `_write_history()` call clobbered the first's new entry.
- `_write_history()` internally re-acquires `_history_lock` — so with a plain Lock, `append_signal_history()` holding the lock and then calling `_write_history()` would deadlock. RLock allows re-entry from the same thread.

**How to apply:**
- Line with `_history_lock = threading.Lock()` → `threading.RLock()`.
- Wrap the body of `append_signal_history()` with `with _history_lock:` covering `_read_history()`, dedup check, `.append()`, and `_write_history()`.
- `return True` lives outside the `with` block (fine — the decision is already made).
