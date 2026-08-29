import requests
import json

# Test pipeline status endpoint
status_resp = requests.get("http://127.0.0.1:8000/api/v1/beta/pipeline/status")
print("Status Code:", status_resp.status_code)
data = status_resp.json()
print("Pipeline Account:", data.get("account"))
print("Scanner Candidates:", len(data.get("stages", {}).get("2_scanner", {}).get("top_candidates", [])))

# Trigger cycle
cycle_resp = requests.post("http://127.0.0.1:8000/api/v1/beta/pipeline/run_cycle")
print("Cycle trigger status:", cycle_resp.status_code)
cycle_data = cycle_resp.json()
print("Cycle Result:", cycle_data.get("cycle_id"), "| Executed orders:", cycle_data.get("executed_orders"))
