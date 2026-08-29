"""Token caching utility with daily expiration for Indian broker sessions."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any

# Asia/Kolkata timezone (UTC+5:30)
IST_TZ = timezone(timedelta(hours=5, minutes=30), name="IST")


class TokenCache:
    """Safely store and retrieve broker authentication tokens on an IST daily basis."""

    def __init__(self, cache_file: str = "storage/token_cache.json"):
        self.cache_file = Path(cache_file)
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @staticmethod
    def _today_ist() -> str:
        return datetime.now(IST_TZ).date().isoformat()

    def get_token(self, broker_name: str) -> Optional[str]:
        """Get cached access token if created today in IST."""
        with self._lock:
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

                today_str = self._today_ist()
                if saved_date_str == today_str and token:
                    return token
                return None
            except Exception:
                return None

    def save_token(self, broker_name: str, token: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Save access token associated with today's IST date atomically."""
        with self._lock:
            data: Dict[str, Any] = {}
            if self.cache_file.exists():
                try:
                    with open(self.cache_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = {}

            data[broker_name] = {
                "token": token,
                "date": self._today_ist(),
                "updated_at": datetime.now(IST_TZ).isoformat(),
                "metadata": metadata or {},
            }

            self._atomic_write(data)

    def clear(self, broker_name: Optional[str] = None) -> None:
        """Clear cache for a specific broker or completely."""
        with self._lock:
            if not self.cache_file.exists():
                return

            if broker_name is None:
                self.cache_file.unlink(missing_ok=True)
                return

            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data.pop(broker_name, None)
                self._atomic_write(data)
            except Exception:
                pass

    def _atomic_write(self, data: Dict[str, Any]) -> None:
        """Write JSON safely via temporary file replacement."""
        dir_name = self.cache_file.parent
        with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8") as tf:
            json.dump(data, tf, indent=2)
            temp_path = Path(tf.name)
        temp_path.replace(self.cache_file)

