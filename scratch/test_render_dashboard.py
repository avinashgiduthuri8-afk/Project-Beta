import sys
from pathlib import Path
import asyncio

# Set utf-8 stdout encoding for Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import pull_state_payload, templates, DASHBOARD_API_KEY
from bots.scanner_bot.scanner import get_signals, get_stats, get_watchlist
from starlette.requests import Request

async def test_render():
    print("Testing pull_state_payload()...")
    state = await pull_state_payload()
    print("pull_state_payload OK.")
    
    print("Testing get_signals, get_stats, get_watchlist...")
    signals = get_signals()
    stats = get_stats()
    watchlist = get_watchlist()
    print("data sources OK.")

    print("Testing template render...")
    # Mock Request scope
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "session": {"authenticated": True}
    }
    req = Request(scope)
    
    resp = templates.TemplateResponse(
        request=req,
        name="dashboard.html",
        context={
            "request": req,
            "data": state,
            "signals": signals,
            "stats": stats,
            "watchlist": watchlist,
            "dashboard_api_key": DASHBOARD_API_KEY or "",
        }
    )
    print("Render completed! Response length:", len(resp.body))

if __name__ == "__main__":
    asyncio.run(test_render())
