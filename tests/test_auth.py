"""Unit tests for Auth and TOTP Session Management."""

import pytest
import pyotp
from auth.session_manager import SessionManager
from auth.token_cache import TokenCache


def test_totp_generation():
    secret = pyotp.random_base32()
    totp_code = SessionManager.generate_totp(secret)
    assert isinstance(totp_code, str)
    assert len(totp_code) == 6
    assert totp_code.isdigit()


def test_token_cache_save_and_retrieve(temp_cache_file):
    cache = TokenCache(cache_file=temp_cache_file)
    assert cache.get_token("zerodha") is None

    cache.save_token("zerodha", "test_token_12345", {"user": "AB1234"})
    retrieved = cache.get_token("zerodha")
    assert retrieved == "test_token_12345"

    cache.clear("zerodha")
    assert cache.get_token("zerodha") is None
