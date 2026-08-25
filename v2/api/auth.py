"""
V2 API Authentication.

Reuses the same X-API-Key mechanism as V1, validated against
DASHBOARD_API_KEY from V2Config.  Auth is fail-closed: if the
secret is unset the dependency raises 500, not 401, so the
deployment gap is immediately visible.
"""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Header, HTTPException, status

from v2.core.config import get_config


async def require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> None:
    """
    FastAPI dependency — inject into any route that requires auth.

    Usage:
        @router.get("/protected", dependencies=[Depends(require_api_key)])
        async def protected(): ...
    """
    cfg = get_config()
    expected = cfg.dashboard_api_key

    if not expected:  # None or empty string — both are misconfiguration
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DASHBOARD_API_KEY is not configured on this server.",
        )

    if x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header required.",
        )

    if not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )
