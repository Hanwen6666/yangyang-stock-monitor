#!/bin/bash
# Webhook 自动部署脚本 — server 端
# GitHub webhook 触发（或者 streamlit cloud container 替换的话改调用）：

# 1. 拉最新代码
cd /opt/yangyang-stock-monitor
git pull origin main --force

# 2. 重新 build（只改动了 streamlit/ 才需要）
docker compose build --no-cache

# 3. 重启容器（保留 data volume）
docker compose up -d

# 4. 清旧镜像
docker image prune -f

echo "[deploy] done at $(date)"
