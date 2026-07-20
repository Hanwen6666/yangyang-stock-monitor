#!/bin/bash
export PATH=/usr/local/bin:/usr/bin:/bin
export HOME=/root
cd /opt/yangyang-stock-monitor
./venv/bin/python3 scripts/auto_refresh.py >> /opt/yangyang-stock-monitor/log/auto_refresh_cron.log 2>&1
