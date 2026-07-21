# Health Check Server 部署记录

**生效时间**: 2026-07-21 11:45 GMT+8

## systemd unit 文件位置
`/etc/systemd/system/yangyang-health.service`

## 备份
`/opt/yangyang-stock-monitor/.bak-yangyang-health.service-20260721`

## 关键配置
- User=root
- Restart=always, RestartSec=10
- Type=simple
- WorkingDirectory=/opt/yangyang-stock-monitor
- ExecStart=`/opt/yangyang-stock-monitor/venv/bin/python3 /opt/yangyang-stock-monitor/health_server.py`
- Environment=HEALTH_PORT=8081, HEALTH_HOST=0.0.0.0

## 端点
- `GET /`: 文本提示
- `GET /healthz`: 完整健康检查（含 cloudbase_api 连通性）
- `GET /health/live`: liveness
- `GET /health/ready`: readiness

## 验证
- 旧进程 ubuntu PID 3017661 自 7-18 跑了 72 小时,无 systemd 守护
- 新进程 root PID 1750174 → 1750465 (RestartSec=10 自动拉起验证通过)
