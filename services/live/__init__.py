"""Compatibility alias forwarding live -> services.live_bot."""
import sys
import importlib

for sub in ["config", "config.settings", "execution", "execution.router", "monitoring", "portfolio", "positions", "telegram", "main"]:
    try:
        mod = importlib.import_module(f"services.live_bot.{sub}")
        sys.modules[f"live.{sub}"] = mod
        parts = sub.split(".")
        if len(parts) == 1:
            globals()[sub] = mod
    except Exception:
        pass
