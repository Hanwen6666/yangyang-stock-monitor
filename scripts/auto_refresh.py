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

# P5C (2026-07-24): 增量永续 update 机制
# 根因: auto_refresh.py 历史上只 refresh ETF 维度 (refresh_data) + 趋势 csv
#       维度, 从不 refresh INDEX_399006.json + data/stock_kline/*.json 这两个
#       K 线维度 — 强势股轮动交易日期永久卡 init 日期 现象.
# 修复: 每个主步骤前后插入 ensure_fresh, any failure = log only (不阻塞主流程)
try:
    from lib.strategy_v3 import _ensure_benchmark_index_fresh, _tencent_kline_one, STOCK_KLINE_DIR  # noqa
except Exception:  # 依赖路径检查失败也运行 — _ensure_benchmark_index_fresh 在 P5A 已 commit
    _ensure_benchmark_index_fresh = None
    _tencent_kline_one = None
    STOCK_KLINE_DIR = None


def _ensure_stock_klines_fresh(max_codes_per_run: int = 200):
    """P5C (2026-07-24): 永续 update 数据/stock_kline/*.json

    Args:
        max_codes_per_run: 单次最多刷新多少只 (默认 200, 防 cron 5min 频繁时全刷)
                           0 = 不限

    Returns:
        (n_refreshed, n_failed, n_skipped)

    容错:
      - 任何文件级失败 → 静默跳过该只 (不阻塞整体)
      - 函数级异常 → 吞掉, 返回 (-1, -1, -1) 信号给 caller
    """
    import json as _json
    from datetime import date as _date
    if STOCK_KLINE_DIR is None or not STOCK_KLINE_DIR.exists():
        return (-1, -1, -1)
    try:
        files = sorted(STOCK_KLINE_DIR.glob("*.json"))
        today_str = _date.today().isoformat()
        n_refreshed = 0; n_failed = 0; n_skipped = 0; n_checked = 0

        for p in files:
            if max_codes_per_run > 0 and n_checked >= max_codes_per_run:
                break
            stem = p.stem
            if "_" not in stem: continue
            _, code = stem.split("_", 1)
            if len(code) != 6 or not code.isdigit(): continue
            n_checked += 1

            try:
                with p.open() as f:
                    raw = _json.load(f)
                if not raw:
                    continue
                local_last = max(raw.keys())
                if local_last >= today_str:
                    n_skipped += 1
                    continue
                df = _tencent_kline_one(code, n=10)
                # P5C fix: 某些股票 tencent API 不按期望返回 (例: 600001 n=10/100/252/300
                # 	都返回上市后 10/100/252/300 天的 bars 而非末 N 天). 多次调间 retry
                # 	也不同, 判断为"tencent 无 last_n 接口"则入永久失败 failed,
                # 	下次 cron 仍会重试 — 但不太可能修为成功, 是 upstream 限制不是代码 bug.
                if df is None or len(df) == 0:
                    n_failed += 1
                    continue
                df["date"] = df["date"].astype(str)
                df_inc = df[df["date"] > local_last]
                if len(df_inc) == 0:
                    # Retry with larger n to detect "tencent returns first-N-bars" stocks
                    df_retry = _tencent_kline_one(code, n=300)
                    if df_retry is not None and len(df_retry) > 0:
                        df_retry["date"] = df_retry["date"].astype(str)
                        df_inc = df_retry[df_retry["date"] > local_last]
                    if len(df_inc) == 0:
                        # final fallback: try reversed — is the FIRST bar from "list" actually
                        # the "last" by date? tencent 不同 endpoint 可能返回顺序不同
                        n_failed += 1
                        continue
                for _, row in df_inc.iterrows():
                    raw[row["date"]] = {
                        "open": float(row.get("open", 0) or 0),
                        "close": float(row.get("close", 0) or 0),
                        "high": float(row.get("high", 0) or 0),
                        "low": float(row.get("low", 0) or 0),
                        "volume": float(row.get("volume", 0) or 0),
                    }
                tmp = p.with_suffix(".json.tmp")
                with tmp.open("w") as f:
                    _json.dump(raw, f, ensure_ascii=False, sort_keys=True)
                tmp.replace(p)
                n_refreshed += 1
            except Exception:
                n_failed += 1
        return (n_refreshed, n_failed, n_skipped)
    except Exception:
        return (-1, -1, -1)


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

    # P5C: 提前 ensure benchmark JSON (cheap, 首次需网络 ~1s)
    if _ensure_benchmark_index_fresh is not None:
        try:
            _ensure_benchmark_index_fresh()
        except Exception as e:
            log(f"P5C benchmark ensure 异常 (不阻塞): {e}")

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

    # P5C: 永续 update 个股 K 线 (限增量, 限 batch 防止腾讯限流)
    # 默认 200 只/run, cron 5min 频率下也是 ~0 work (今日 last_date 的股会被 skipped)
    try:
        n_r, n_f, n_s = _ensure_stock_klines_fresh(max_codes_per_run=200)
        if n_r >= 0:
            log(f"P5C K线永续 update: refreshed={n_r} failed={n_f} skipped(已fresh)={n_s}")
        else:
            log(f"P5C K线永续 update 未启动 (依赖不可用或 STOCK_KLINE_DIR 不存在)")
    except Exception as e:
        log(f"P5C K线永续 update 异常 (不阻塞): {e}")

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
