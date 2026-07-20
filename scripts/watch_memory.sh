#!/bin/bash
# 监控 streamlit 内存，超过 1.5GB 自动重启 (2026-07-19 加)
PID=$(pgrep -f "streamlit run" | head -1)
if [ -z "$PID" ]; then
    echo "[$(date)] streamlit not running"
    exit 0
fi
RSS_KB=$(awk '/VmRSS/{print $2}' /proc/$PID/status 2>/dev/null)
RSS_MB=$((RSS_KB / 1024))
THRESHOLD=1500
if [ "$RSS_MB" -gt "$THRESHOLD" ]; then
    echo "[$(date)] streamlit RSS=${RSS_MB}MB > ${THRESHOLD}MB, restarting..."
    systemctl restart yangyang-stock.service
    logger -t streamlit-mem-watch "streamlit restarted due to RSS=${RSS_MB}MB > ${THRESHOLD}MB"
else
    echo "[$(date)] streamlit RSS=${RSS_MB}MB (OK, threshold=${THRESHOLD}MB)"
fi
