#!/usr/bin/env bash
# PROJECT-ALPHA VPS One-Touch Update Script

set -e

echo "=================================================="
echo " PROJECT-ALPHA — VPS Update & Restart Utility"
echo "=================================================="

# 1. Pull latest verified commits from GitHub main
echo "[1/4] Pulling latest code from GitHub origin/main..."
git fetch origin main
git reset --hard origin/main

# 2. Check Python environment and install dependencies if updated
echo "[2/4] Verifying dependencies..."
if [ -f "requirements.txt" ]; then
    pip install --no-cache-dir -r requirements.txt
fi

# 3. Check Database integrity
echo "[3/4] Checking database schema & integrity..."
python3 -c "import sqlite3; conn=sqlite3.connect('data/project_alpha.db'); print('Integrity:', conn.execute('PRAGMA integrity_check').fetchone()[0])"

# 4. Restart running process
echo "[4/4] Restarting application..."
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet project-alpha; then
    echo "Restarting via systemd service 'project-alpha'..."
    sudo systemctl restart project-alpha
elif [ -f "docker-compose.yml" ] && command -v docker >/dev/null 2>&1 && docker compose ps --quiet >/dev/null 2>&1; then
    echo "Restarting via Docker Compose..."
    docker compose down && docker compose up -d --build
elif command -v pm2 >/dev/null 2>&1 && pm2 list | grep -q "app"; then
    echo "Restarting via PM2..."
    pm2 restart app
else
    echo "Restarting direct python process..."
    pkill -f "python.*app.py" || true
    nohup python3 app.py > app.log 2>&1 &
    echo "Application restarted in background. Logs: tail -f app.log"
fi

echo "=================================================="
echo " SUCCESS: PROJECT-ALPHA updated to latest build!"
echo "=================================================="
