import os
import sys
import paramiko
import time

VPS_HOST = os.getenv("VPS_HOST")
VPS_PORT = int(os.getenv("VPS_PORT", "22"))
VPS_USER = os.getenv("VPS_USER", "root")
VPS_PASSWORD = os.getenv("VPS_ROOT_PASSWORD")
VPS_KEY_PATH = os.getenv("VPS_KEY_PATH")

if not VPS_HOST or (not VPS_PASSWORD and not VPS_KEY_PATH):
    sys.exit("Error: VPS_HOST and credentials not set. Set VPS_HOST and VPS_ROOT_PASSWORD or VPS_KEY_PATH in environment.")

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
connect_kwargs = {
    "hostname": VPS_HOST,
    "port": VPS_PORT,
    "username": VPS_USER,
}
if VPS_KEY_PATH:
    connect_kwargs["key_filename"] = VPS_KEY_PATH
if VPS_PASSWORD:
    connect_kwargs["password"] = VPS_PASSWORD

client.connect(**connect_kwargs)

def run(cmd, desc):
    print(f"\n=== {desc} ===")
    stdin, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if out:
        print("STDOUT:\n" + out)
    if err:
        print("STDERR:\n" + err)
    return out, err

# 1. Stop service
run("systemctl stop project-alpha", "Stop project-alpha service")

# 2. Reset config_override.json to PAPER mode
override_json = (
    "{\\n"
    '  \\"order_size_inr\\": 200.0,\\n'
    '  \\"deployment_mode\\": \\"PAPER\\",\\n'
    '  \\"trading_enabled\\": true,\\n'
    '  \\"shadow_mode\\": false,\\n'
    '  \\"v2_deployment_mode\\": \\"PAPER\\",\\n'
    '  \\"v2_trading_enabled\\": true,\\n'
    '  \\"v2_shadow_mode\\": false\\n'
    "}"
)
run(f'printf "{override_json}" > /opt/project-alpha/data/config_override.json', "Write config_override.json")
run("cat /opt/project-alpha/data/config_override.json", "Verify config_override.json")

# 3. Wipe old positions from VPS SQLite databases
wipe_script = """/opt/project-alpha/.venv/bin/python3 -c "
import sqlite3, os

for db_path in ['/opt/project-alpha/v2/data/alpha_v2.db', '/opt/project-alpha/data/project_alpha.db']:
    if not os.path.exists(db_path):
        continue
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute('SELECT name FROM sqlite_master WHERE type=\\'table\\'')
    tables = [r[0] for r in cur.fetchall()]
    if 'positions' in tables:
        cur.execute('DELETE FROM positions')
    if 'orders' in tables:
        cur.execute('DELETE FROM orders')
    if 'order_state_transitions' in tables:
        cur.execute('DELETE FROM order_state_transitions')
    if 'production_ops_state' in tables:
        cur.execute(\\"UPDATE production_ops_state SET deployment_mode='PAPER' WHERE id=1\\")
    conn.commit()
    cur.execute('SELECT count(*) FROM positions')
    print(f'{db_path} positions count: {cur.fetchone()[0]}')
    conn.close()
" """
run(wipe_script, "Verify 0 positions in VPS databases")

# 4. Restart project-alpha service
run("systemctl restart project-alpha", "Restart project-alpha service")
time.sleep(5)

# 5. Check systemctl status
run("systemctl status project-alpha --no-pager", "Check systemctl status")

# 6. Check recent application logs
run("journalctl -u project-alpha -n 30 --no-pager", "Check recent journalctl logs")

client.close()
