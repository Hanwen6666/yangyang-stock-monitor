"""
清理旧日志、过期数据缓存
  - 清理 7 天前的项目日志
  - 清理 7 天前 Streamlit 自动重算产物
  - 清理 30 天前 K 线缓存 pickle

由 cron 每天 4:30 跑一次
"""
import os
import time
from pathlib import Path

PROJECT_LOG = Path("/opt/yangyang-stock-monitor/log")
NGINX_LOG = Path("/data/log/nginx")
DATA_DIR = Path("/opt/yangyang-stock-monitor/data")
KLINE_CACHE = DATA_DIR / "kline_cache"

DAYS_LOG = 7
DAYS_KLINE = 30


def _older_than(p: Path, days: int) -> bool:
    try:
        return (time.time() - p.stat().st_mtime) > days * 86400
    except OSError:
        return False


def _clean_dir(d: Path, days: int, pattern: str = "*"):
    if not d.exists():
        return 0
    n = 0
    for f in d.glob(pattern):
        if f.is_file() and _older_than(f, days):
            try:
                f.unlink()
                n += 1
            except Exception:
                pass
    return n


def main():
    print("=== cleanup_logs ===")
    n1 = _clean_dir(PROJECT_LOG, DAYS_LOG)
    print(f"project log: 删 {n1} 个 {DAYS_LOG} 天前的文件")
    n2 = _clean_dir(NGINX_LOG, DAYS_LOG, "*.log*")
    print(f"nginx log: 删 {n2} 个 {DAYS_LOG} 天前的文件")
    n3 = _clean_dir(KLINE_CACHE, DAYS_KLINE, "*.pkl")
    print(f"kline cache: 删 {n3} 个 {DAYS_KLINE} 天前的文件")
    # 报告剩余
    for d in (PROJECT_LOG, NGINX_LOG, KLINE_CACHE):
        if d.exists():
            total = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            print(f"  {d}: 剩余 {total / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
