"""Compatibility alias forwarding paper -> services.paper_bot."""
import sys
import importlib

for sub in ["config", "config.settings", "execution", "execution.router", "monitoring", "portfolio", "positions", "telegram", "main", "indian_service"]:
    try:
        mod = importlib.import_module(f"services.paper_bot.{sub}")
        sys.modules[f"paper.{sub}"] = mod
        parts = sub.split(".")
        if len(parts) == 1:
            globals()[sub] = mod
    except Exception:
        pass
