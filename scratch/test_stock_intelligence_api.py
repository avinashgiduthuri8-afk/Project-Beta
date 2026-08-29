import requests
import json

base_url = "http://127.0.0.1:8000"

print("--- 1. Testing GET /api/v1/beta/stock/RELIANCE ---")
r1 = requests.get(f"{base_url}/api/v1/beta/stock/RELIANCE")
print("Status:", r1.status_code)
d1 = r1.json()
print("Stock:", d1.get("data", {}).get("symbol"), "| Name:", d1.get("data", {}).get("profile", {}).get("name"))
print("LTP:", d1.get("data", {}).get("profile", {}).get("ltp"), "| Score:", d1.get("data", {}).get("total_score"))

print("\n--- 2. Testing POST /api/v1/beta/stock/RELIANCE/backtest ---")
r2 = requests.post(f"{base_url}/api/v1/beta/stock/RELIANCE/backtest")
print("Status:", r2.status_code)
d2 = r2.json()
bt = d2.get("data", {})
print("Trades:", bt.get("total_trades"), "| Win Rate:", bt.get("win_rate_pct"), "| Net PnL (INR):", bt.get("net_pnl_inr"))

print("\n--- 3. Testing POST /api/v1/beta/stock/RELIANCE/predict ---")
r3 = requests.post(f"{base_url}/api/v1/beta/stock/RELIANCE/predict")
print("Status:", r3.status_code)
d3 = r3.json()
pr = d3.get("data", {})
print("1D Target:", pr.get("trend_1d", {}).get("target"), "| 5D Target:", pr.get("trend_5d", {}).get("target"), "| Confidence:", pr.get("model_confidence_pct"))

print("\n--- 4. Testing POST /api/v1/beta/stock/INFY/watchlist_toggle ---")
r4 = requests.post(f"{base_url}/api/v1/beta/stock/INFY/watchlist_toggle")
print("Status:", r4.status_code)
print("Response:", r4.json().get("status"))

print("\n--- 5. Testing VGX Grid Config Endpoint ---")
r5 = requests.get(f"{base_url}/api/vgx/grid-config", headers={"X-API-Key": "beta-dev-key"})
print("Status:", r5.status_code)
print("Grid Stocks:", r5.json().get("grid_coins"))
