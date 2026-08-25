"""Automated session management with TOTP (pyotp) 2FA login for Indian brokers."""

from __future__ import annotations

import logging
import os
from typing import Optional, Dict, Any
import pyotp
from auth.token_cache import TokenCache

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages 2FA TOTP token generation and automated daily broker authentication."""

    def __init__(self, token_cache: Optional[TokenCache] = None):
        self.token_cache = token_cache or TokenCache()

    @staticmethod
    def generate_totp(totp_secret: str) -> str:
        """Generate current 6-digit Time-based One-Time Password (TOTP) from base32 secret."""
        if not totp_secret:
            raise ValueError("TOTP secret key cannot be empty.")
        clean_secret = totp_secret.replace(" ", "").upper()
        totp = pyotp.TOTP(clean_secret)
        return totp.now()

    def get_or_create_session(
        self,
        broker_name: str,
        auth_func: callable,
        force_refresh: bool = False,
    ) -> str:
        """Retrieve valid cached token or execute fresh authentication workflow."""
        if not force_refresh:
            cached_token = self.token_cache.get_token(broker_name)
            if cached_token:
                logger.info(f"Using cached daily session token for {broker_name}.")
                return cached_token

        logger.info(f"Initiating fresh daily authentication for {broker_name}...")
        token, metadata = auth_func()
        self.token_cache.save_token(broker_name, token, metadata)
        logger.info(f"Successfully authenticated and cached token for {broker_name}.")
        return token
