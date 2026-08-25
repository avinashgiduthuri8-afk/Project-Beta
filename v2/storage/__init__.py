"""
V2 Storage layer — Skeleton only.

Planned: async-safe storage adapters (JSON, SQLite, PostgreSQL).
V1 uses threading.Lock() + JSON files — V2 will wrap these in async adapters
so storage is swappable without touching service logic.

Not implemented yet. No imports from V1.
"""
