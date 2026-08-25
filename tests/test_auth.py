"""
Unit tests for TOTP Authentication and Session Token Caching (Prompt A).
"""

from __future__ import annotations

import os
from auth.session_manager import SessionManager
from auth.token_cache import TokenCache


def test_totp_generation():
    # Valid Base32 secret for testing (JBSWY3DPEHPK3PXP is standard RFC test secret)
    secret = "JBSWY3DPEHPK3PXP"
    otp = SessionManager.generate_totp(secret)
    assert isinstance(otp, str)
    assert len(otp) == 6
    assert otp.isdigit()


def test_token_cache_save_and_retrieve():
    cache_path = ".test_session_cache.json"
    cache = TokenCache(cache_path)
    try:
        cache.save_token("ZERODHA", "test_access_token_123", extra_metadata={"user": "TEST"})
        retrieved = cache.get_token("ZERODHA")
        assert retrieved == "test_access_token_123"

        # Check non-existent broker
        assert cache.get_token("NON_EXISTENT") is None

        # Clear
        cache.clear("ZERODHA")
        assert cache.get_token("ZERODHA") is None
    finally:
        if os.path.exists(cache_path):
            os.remove(cache_path)
