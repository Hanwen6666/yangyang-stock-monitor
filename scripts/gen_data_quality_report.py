#!/usr/bin/env python3
"""
羊羊股市监测 - 数据质量报告生成器

功能: 扫描 results.csv / K线 cache / etf_trend_history.csv / .asof / dead_stocks.json
      输出 7 项数据质量指标到 data/data_quality_report.json

指标:
  1. asof_freshness_days  - 数据距今天数 (越大越陈旧)
  2. results_row_count    - results.csv 行数 (期望 207)
  3. kline_cache_coverage - K线 cache 覆盖率 (0-100%)
  4. change_pct_zero_rate - change_pct=0 的占比 (越大越异常)
  5. trend_history_dates   - etf_trend_history.csv 唯一日期数 (期望 >=25)
  6. dead_stocks_count     - 黑名单数量 (过大=异常)
  7. stale_files           - 哪些数据文件 24h 内未更新

2026-07-22 「完美」全栈排查沉淀.
"""
from __future__ import annotations
import json
import time
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data"
REPORT_PATH = DATA_DIR / "data_quality_report.json"


def load_results() -> dict:
    """扫描 results.csv"""
    import pandas as pd
    p = DATA_DIR / "results.csv"
    if not p.exists():
        return {"exists": False}
    df = pd.read_csv(p)
    asof = df["asof_date"].astype(str).str.replace("-", "").iloc[0] if "asof_date" in df.columns else None
    return {
        "exists": True,
        "rows": len(df),
        "asof_date": asof,
        "columns": list(df.columns),
        "change_pct_zero_count": int((df.get("change_pct", 0) == 0).sum()) if "change_pct" in df.columns else None,
        "change_pct_zero_rate": float((df.get("change_pct", 0) == 0).mean()) if "change_pct" in df.columns else None,
        "strength_label_distribution": df["strength_label"].value_counts().to_dict() if "strength_label" in df.columns else {},
    }


def load_kline_cache() -> dict:
    """扫描 K线 cache 覆盖率"""
    import pickle
    cache_dir = DATA_DIR / ".kline_cache"
    if not cache_dir.exists():
        return {"exists": False}
    today = date.today().strftime("%Y%m%d")
    pkl = cache_dir / f"klines_{today}.pkl"
    if not pkl.exists():
        # 用最新 pkl
        pkls = sorted(cache_dir.glob("klines_*.pkl"))
        if not pkls:
            return {"exists": False}
        pkl = pkls[-1]
    try:
        with open(pkl, "rb") as f:
            cache = pickle.load(f)
        return {
            "exists": True,
            "cache_file": pkl.name,
            "cache_mtime": datetime.fromtimestamp(pkl.stat().st_mtime).isoformat(),
            "codes_count": len(cache),
        }
    except Exception as e:
        return {"exists": False, "error": str(e)}


def load_trend_history() -> dict:
    """扫描 etf_trend_history.csv"""
    import pandas as pd
    p = DATA_DIR / "etf_trend_history.csv"
    if not p.exists():
        return {"exists": False}
    df = pd.read_csv(p)
    date_cols = [c for c in df.columns if c.startswith("d_")]
    return {
        "exists": True,
        "rows": len(df),
        "date_columns": len(date_cols),
        "date_range": f"{date_cols[0]}..{date_cols[-1]}" if date_cols else None,
        "first_date": date_cols[0] if date_cols else None,
        "last_date": date_cols[-1] if date_cols else None,
    }


def load_dead_stocks() -> dict:
    """扫描 dead_stocks.json 黑名单

    注意: dead_stocks.json 实际位于 /home/ubuntu/.openclaw/workspace/scripts/market_strength/data/
    (不在 /opt/yangyang-stock-monitor/data/), 因为它属于 fetch_incremental.py cron 体系
    """
    # 跨多个可能位置查找
    possible_paths = [
        Path("/home/ubuntu/.openclaw/workspace/scripts/market_strength/data/dead_stocks.json"),
        DATA_DIR / "dead_stocks.json",
    ]
    for p in possible_paths:
        if p.exists():
            try:
                with open(p) as f:
                    ds = json.load(f)
                if isinstance(ds, list):
                    return {"exists": True, "path": str(p), "dead_count": len(ds), "dead_codes_sample": ds[:5]}
                elif isinstance(ds, dict):
                    dead = ds.get("dead", [])
                    return {"exists": True, "path": str(p), "dead_count": len(dead), "dead_codes_sample": dead[:5]}
                else:
                    return {"exists": True, "path": str(p), "type": type(ds).__name__}
            except Exception as e:
                return {"exists": False, "path": str(p), "error": str(e)}
    return {"exists": False, "searched_paths": [str(p) for p in possible_paths]}


def check_asof() -> dict:
    """检查 .asof 文件与今天差异"""
    p = DATA_DIR / ".asof"
    if not p.exists():
        return {"exists": False}
    asof_str = p.read_text().strip()
    try:
        asof_date = datetime.strptime(asof_str, "%Y%m%d").date()
        today = date.today()
        freshness_days = (today - asof_date).days
        return {
            "exists": True,
            "asof": asof_str,
            "today": today.strftime("%Y%m%d"),
            "freshness_days": freshness_days,
            "is_fresh": freshness_days <= 1,
        }
    except Exception as e:
        return {"exists": True, "error": str(e), "raw": asof_str}


def find_stale_files(threshold_hours: int = 24) -> list:
    """找出 24h 内未更新的关键数据文件"""
    threshold = time.time() - threshold_hours * 3600
    key_files = [
        "results.csv",
        ".asof",
        "etf_trend_history.csv",
    ]
    stale = []
    for name in key_files:
        p = DATA_DIR / name
        if p.exists() and p.stat().st_mtime < threshold:
            age_h = (time.time() - p.stat().st_mtime) / 3600
            stale.append({"file": name, "age_hours": round(age_h, 1)})
    return stale


def main():
    report = {
        "generated_at": datetime.now().isoformat(),
        "generated_at_utc": datetime.utcnow().isoformat(),
        "data_quality": {
            "asof": check_asof(),
            "results_csv": load_results(),
            "kline_cache": load_kline_cache(),
            "trend_history": load_trend_history(),
            "dead_stocks": load_dead_stocks(),
            "stale_files": find_stale_files(),
        },
    }

    # 综合健康判断
    asof = report["data_quality"]["asof"]
    res = report["data_quality"]["results_csv"]
    cache = report["data_quality"]["kline_cache"]
    
    health_status = "ok"
    health_reasons = []
    if not asof.get("is_fresh", False):
        health_status = "warn"
        health_reasons.append(f"asof 陈旧 {asof.get('freshness_days', '?')} 天")
    if res.get("rows", 0) < 200:
        health_status = "warn"
        health_reasons.append(f"results.csv 仅 {res.get('rows', 0)} 行 (期望 207)")
    if cache.get("codes_count", 0) < 200:
        health_status = "warn"
        health_reasons.append(f"K线 cache 仅 {cache.get('codes_count', 0)} 只 (期望 207)")
    if report["data_quality"]["stale_files"]:
        health_status = "warn"
        health_reasons.append(f"{len(report['data_quality']['stale_files'])} 个数据文件 24h+ 未更新")

    report["overall"] = {
        "status": health_status,
        "reasons": health_reasons,
    }

    # 写入文件
    DATA_DIR.mkdir(exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    print(f"[{datetime.now().isoformat()}] 数据质量报告已生成: {REPORT_PATH}")
    print(f"[overall] {health_status} - {' / '.join(health_reasons) if health_reasons else '全部数据新鲜完整'}")
    print()
    print(json.dumps(report["overall"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()