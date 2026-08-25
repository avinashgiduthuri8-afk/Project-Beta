"""
TOTP Auto-Login & Broker Session Manager.
Generates 2FA TOTP codes using pyotp and manages daily authentication workflows.
"""

from __future__ import annotations

import logging
from typing import Optional
import pyotp
from auth.token_cache import TokenCache

logger = logging.getLogger(__name__)


class SessionManager:
    """Handles TOTP generation and session verification for Indian Broker APIs."""

    def __init__(self, cache_file_path: str = ".session_cache.json") -> None:
        self.cache = TokenCache(cache_file_path)

    @staticmethod
    def generate_totp(totp_secret: str) -> str:
        """
        Generate a current 6-digit Time-Based One-Time Password (TOTP).

        Args:
            totp_secret: Base32 encoded TOTP secret key from broker 2FA settings.

        Returns:
            6-digit TOTP string.
        """
        if not totp_secret:
            raise ValueError("TOTP secret cannot be empty.")
        clean_secret = totp_secret.replace(" ", "").upper()
        totp = pyotp.TOTP(clean_secret)
        otp_code = totp.now()
        logger.debug("Generated fresh TOTP code for 2FA login")
        return otp_code

    def get_valid_session(self, broker_name: str) -> Optional[str]:
        """Fetch active access token from cache if valid for today."""
        return self.cache.get_token(broker_name)

    def cache_session(
        self,
        broker_name: str,
        access_token: str,
        public_token: Optional[str] = None,
    ) -> None:
        """Store newly generated access token into cache."""
        self.cache.save_token(broker_name, access_token, public_token)

    def invalidate_session(self, broker_name: str) -> None:
        """Invalidate/clear session when broker returns 401/403 or token expires."""
        logger.warning(f"Invalidating session for {broker_name}")
        self.cache.clear(broker_name)
