"""Token caching utility with daily expiration for Indian broker sessions."""

from __future__ import annotations

import json
import os
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Dict, Any


class TokenCache:
    """Safely store and retrieve broker authentication tokens on a daily basis."""

    def __init__(self, cache_file: str = "storage/token_cache.json"):
        self.cache_file = Path(cache_file)
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)

    def get_token(self, broker_name: str) -> Optional[str]:
        """Get cached access token if created today."""
        if not self.cache_file.exists():
            return None

        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data: Dict[str, Any] = json.load(f)

            broker_data = data.get(broker_name)
            if not broker_data:
                return None

            saved_date_str = broker_data.get("date")
            token = broker_data.get("token")

            today_str = date.today().isoformat()
            if saved_date_str == today_str and token:
                return token
            return None
        except Exception:
            return None

    def save_token(self, broker_name: str, token: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Save access token associated with today's date."""
        data: Dict[str, Any] = {}
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}

        data[broker_name] = {
            "token": token,
            "date": date.today().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "metadata": metadata or {},
        }

        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def clear(self, broker_name: Optional[str] = None) -> None:
        """Clear cache for a specific broker or completely."""
        if not self.cache_file.exists():
            return

        if broker_name is None:
            self.cache_file.unlink(missing_ok=True)
            return

        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.pop(broker_name, None)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass
