"""
自动数据刷新守护脚本

逻辑：
  - 每小时跑一次
  - 判断今日是否需要更新 API 数据（asof 不到今天就拉）
  - 判断今日是否需要重算趋势（看当前时间和趋势最新日期）
  - 都完成后返回

由 cron 触发，stdout 写入 /data/log/auto_refresh.log
"""
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# 把项目根加进 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fetch_data import refresh_data, recompute_locally, DATA_DIR  # noqa


def main():
    t0 = time.time()
    log_lines = []
    def log(msg):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        log_lines.append(line)

    log("=" * 50)
    log("auto_refresh started")

    # 1) 判断是否需要拉 API
    asof_path = DATA_DIR / ".asof"
    today = datetime.now().strftime("%Y%m%d")
    current_asof = asof_path.read_text(encoding="utf-8").strip() if asof_path.exists() else ""
    needs_network = current_asof != today
    log(f"current_asof={current_asof or 'EMPTY'} today={today} needs_network={needs_network}")

    if needs_network:
        try:
            res = refresh_data()
            if res.get("ok"):
                log(f"API 拉取成功: asof={res.get('asof_date')} n_etfs={res.get('n_etfs')} elapsed={res.get('elapsed_ms')}ms")
            else:
                log(f"API 拉取失败: {res.get('error')}")
        except Exception as e:
            log(f"API 拉取异常: {e}")
    else:
        log("API 数据已是最新，跳过")

    # 2) 判断是否需要重算趋势
    now = datetime.now()
    target_date = today if now.hour >= 15 else (now - timedelta(days=1)).strftime("%Y%m%d")
    log(f"趋势目标日期: {target_date} (当前时间: {now.strftime('%H:%M')})")

    hist_p = DATA_DIR / "etf_trend_history.csv"
    latest_trend_date = ""
    if hist_p.exists():
        import pandas as pd
        hdr = pd.read_csv(hist_p, nrows=0).columns.tolist()
        for col in hdr:
            if not col.startswith("d_"):
                continue
            d = col[2:]
            # 支持 d_YYYYMMDD (11) 和 d_YYYY-MM-DD (13) 两种格式
            d_compact = d.replace("-", "").replace("/", "")
            if d_compact.isdigit() and len(d_compact) == 8:
                if d_compact > latest_trend_date:
                    latest_trend_date = d_compact
    log(f"趋势最新日期: {latest_trend_date or 'EMPTY'}")

    if target_date != latest_trend_date:
        log("开始重算趋势...")
        try:
            res = recompute_locally()
            if res.get("ok"):
                log(f"重算完成: n_etfs={res.get('n_etfs')} n_points={res.get('n_points')}")
            else:
                log(f"重算失败: {res.get('error')}")
        except Exception as e:
            log(f"重算异常: {e}")
    else:
        log("趋势已是最新，跳过")

    # 重算完成后顺便预生成趋势 HTML 静态文件
    try:
        from scripts import precompute_history
        precompute_history.main()
    except Exception as e:
        log(f"预生成趋势 HTML 异常: {e}")

    elapsed = int((time.time() - t0) * 1000)
    log(f"auto_refresh done, total {elapsed}ms")

    # 写日志（项目内 log 目录）
    log_dir = Path(__file__).parent.parent / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "auto_refresh.log"
    try:
        with log_file.open("a", encoding="utf-8") as f:
            f.write("\n".join(log_lines) + "\n")
    except PermissionError:
        # 退而写 /tmp
        with open("/tmp/auto_refresh.log", "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines) + "\n")


if __name__ == "__main__":
    main()
