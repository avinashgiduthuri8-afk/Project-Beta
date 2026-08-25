"""
Session and Auth Token Cache Manager.
Stores broker tokens locally to prevent redundant authentication calls.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class TokenCache:
    """Manages cached access tokens per broker with expiration checks."""

    def __init__(self, cache_file_path: str = ".session_cache.json") -> None:
        self.cache_file = Path(cache_file_path)

    def _read_cache(self) -> Dict[str, Any]:
        if not self.cache_file.exists():
            return {}
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read session cache: {e}. Resetting cache.")
            return {}

    def _write_cache(self, data: Dict[str, Any]) -> None:
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to write session cache: {e}")

    def get_token(self, broker_name: str) -> Optional[str]:
        """
        Get cached token for broker if it is valid for today (Indian brokers invalidate daily).
        """
        data = self._read_cache()
        broker_data = data.get(broker_name.upper())
        if not broker_data:
            return None

        # Check token date (Indian broker sessions expire daily after market close or midnight)
        token_date = broker_data.get("date")
        today_str = date.today().isoformat()

        if token_date != today_str:
            logger.info(f"Cached token for {broker_name} has expired (issued: {token_date}, today: {today_str})")
            return None

        access_token = broker_data.get("access_token")
        return access_token

    def save_token(
        self,
        broker_name: str,
        access_token: str,
        public_token: Optional[str] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Save active access token with today's timestamp."""
        data = self._read_cache()
        data[broker_name.upper()] = {
            "access_token": access_token,
            "public_token": public_token,
            "date": date.today().isoformat(),
            "created_at": datetime.now().isoformat(),
            "metadata": extra_metadata or {},
        }
        self._write_cache(data)
        logger.info(f"Saved fresh session token for {broker_name} to cache.")

    def clear(self, broker_name: Optional[str] = None) -> None:
        """Clear cached tokens."""
        if broker_name:
            data = self._read_cache()
            if broker_name.upper() in data:
                del data[broker_name.upper()]
                self._write_cache(data)
        else:
            if self.cache_file.exists():
                os.remove(self.cache_file)
