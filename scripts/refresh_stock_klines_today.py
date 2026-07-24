"""
P5 (2026-07-24) 一键刷新工具: 一次性 refresh 全市场个股 K 线 (滞后追平)

根因:
  - 2026-07-22 之前, fetch_incremental.py 的 cron / auto_refresh.py 路径中
    没有任何代码增量更新 INDEX_399006.json 与 stock_kline/sh_*.json
  - 2026-07-17 → 2026-07-24 整周, 强势股轮动 Tab 显示交易日期停在 2026-07-17,
    因为 _resolve_asof_date 的 p90 算法被 4539 只个股 last_date=2026-07-17 压住

本工具:
  1. 扫 EXTERNAL_DATA_DIR/stock_kline/{sh,sz,bj}_<code6>.json
  2. 对每个文件: 若 last_date < today, 从腾讯源拉 last 10 days K 线增量 append
  3. 容错: 腾讯失败 → 静默跳过该只 (不阻塞整体), stderr 一行 warn

执行:
  venv/bin/python3 scripts/refresh_stock_klines_today.py

输出:
  打印 n_total / n_refreshed / n_failed / elapsed_ms / stats
  个股 JSON 文件被原地 update (追加历史, 不覆盖已有)

不变量:
  - 不动 fetcher / 算法 / cron / 任何 lib/strategy_v3.py
  - 不动 pickle cache (自动 cron 下次跑会自然重建)
  - 一次性工具, 不替代 fetch_incremental.py (它才是永续 update)
"""
import sys
import json
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.strategy_v3 import (
    EXTERNAL_DATA_DIR,
    STOCK_KLINE_DIR,
    _tencent_kline_one,
)


def _parse_kline_json(p: Path) -> dict:
    """复用 strategy_v3._parse_kline_json 的等价逻辑 (避免跨包依赖)"""
    try:
        with p.open() as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def main():
    if not STOCK_KLINE_DIR.exists():
        print(f"❌ STOCK_KLINE_DIR 不存在: {STOCK_KLINE_DIR}")
        sys.exit(1)

    t0 = time.time()
    files = sorted(STOCK_KLINE_DIR.glob("*.json"))
    print(f"📊 扫描 {len(files)} 个 K 线文件 in {STOCK_KLINE_DIR}")

    n_total = 0
    n_refreshed = 0
    n_already_fresh = 0
    n_failed = 0
    n_skipped_short = 0
    from datetime import date as _date
    today_str = _date.today().isoformat()
    failed_codes = []

    for i, p in enumerate(files):
        stem = p.stem  # e.g. sh_600519
        if "_" not in stem:
            continue
        _, code = stem.split("_", 1)
        if len(code) != 6 or not code.isdigit():
            continue
        n_total += 1

        raw = _parse_kline_json(p)
        if not raw:
            n_skipped_short += 1
            continue
        local_last = max(raw.keys())
        if local_last >= today_str:
            n_already_fresh += 1
            continue

        try:
            df = _tencent_kline_one(code, n=10)
            if df is None or len(df) == 0:
                n_failed += 1
                failed_codes.append(code)
                continue
            df["date"] = df["date"].astype(str)
            df_incremental = df[df["date"] > local_last]
            # P5C fix: 有些股票 tencent n=10 返回上市后 10 天 (2009-12) 而非末 10 天 (2026-07),
            # 	此时 df_incremental=空 -> 跳过 — 但股从未增量. 用 n=300 重试覆盖.
            if len(df_incremental) == 0:
                df_retry = _tencent_kline_one(code, n=300)
                if df_retry is not None and len(df_retry) > 0:
                    df_retry["date"] = df_retry["date"].astype(str)
                    df_incremental = df_retry[df_retry["date"] > local_last]
                if len(df_incremental) == 0:
                    n_failed += 1
                    failed_codes.append(code)
                    continue

            for _, row in df_incremental.iterrows():
                raw[row["date"]] = {
                    "open": float(row.get("open", 0) or 0),
                    "close": float(row.get("close", 0) or 0),
                    "high": float(row.get("high", 0) or 0),
                    "low": float(row.get("low", 0) or 0),
                    "volume": float(row.get("volume", 0) or 0),
                }
            tmp = p.with_suffix(".json.tmp")
            with tmp.open("w") as f:
                json.dump(raw, f, ensure_ascii=False, sort_keys=True)
            tmp.replace(p)
            n_refreshed += 1
        except Exception:
            n_failed += 1
            failed_codes.append(code)

        if (i + 1) % 500 == 0:
            elapsed_so_far = (time.time() - t0) * 1000
            print(f"  [{i + 1}/{len(files)}] {elapsed_so_far:.0f}ms elapsed, "
                  f"refreshed={n_refreshed}, failed={n_failed}, fresh={n_already_fresh}")

    elapsed_ms = int((time.time() - t0) * 1000)
    print()
    print(f"✅ 完成 · elapsed={elapsed_ms}ms")
    print(f"  n_total (with valid code): {n_total}")
    print(f"  n_refreshed:               {n_refreshed}")
    print(f"  n_already_fresh:           {n_already_fresh}")
    print(f"  n_failed (tengxun):        {n_failed}")
    print(f"  n_skipped_short (bad json):{n_skipped_short}")
    if failed_codes and len(failed_codes) <= 30:
        print(f"  failed codes: {failed_codes}")
    elif failed_codes:
        print(f"  failed first 30: {failed_codes[:30]}  (total: {len(failed_codes)})")


if __name__ == "__main__":
    main()
