"""
数据获取 / 本地重算

两种刷新方式:
  1. refresh_data()        - 从 CloudBase API 拉(快速,~1s)
  2. recompute_locally()   - 用 v27 算法本地重算(慢,~5-10min,基于当前时间点 K 线)
"""
import argparse
import csv
import json
import sys
import urllib.request
import urllib.error
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DEFAULT_BASE = "https://agentchat-d0gsw7sn6c36f0b00.service.tcloudbase.com/api/etf-strength"


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "yangyang-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def write_csv(path, rows, fieldnames):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _build_results_csv_from_metrics(metrics_df, asof_date):
    """把算法输出转成 results.csv 格式"""
    # 合并 category/name/fund_size
    pool_path = DATA_DIR / "etf_pool.csv"
    cat_map = {}
    name_map = {}
    fund_size_map = {}
    try:
        if pool_path.exists():
            pool = pd.read_csv(pool_path, dtype={"代码": str})
            # 分类列:有 theme 用 theme,有 cluster 用 cluster,都没有就"其他"
            cat_col = None
            for c in ["theme", "cluster", "category"]:
                if c in pool.columns: cat_col = c; break
            cat_map = dict(zip(pool["代码"], pool[cat_col].astype(str) if cat_col else "其他"))
            name_map = dict(zip(pool["代码"], pool["名称"]))
            fund_size_map = dict(zip(pool["代码"].astype(str), pool["fund_size_yi"].astype(float)))
    except Exception:
        pass
    except Exception:
        pass

    cols = ["code", "name", "category", "strength_label", "fund_size_yi",
            "slope_20", "slope_50", "slope_120", "sharpe_composite", "adx",
            "up_ratio_60", "n_changes", "n_points", "asof_date"]
    rows = []
    for _, r in metrics_df.iterrows():
        code = str(r["code"]).zfill(6)
        rows.append({
            "code": code,
            "name": name_map.get(code, code),
            "category": cat_map.get(code, "其他"),
            "strength_label": r["strength_label"],
            "fund_size_yi": fund_size_map.get(code, 0),
            "slope_20": r["slope_20"],
            "slope_50": r["slope_50"],
            "slope_120": r["slope_120"],
            "sharpe_composite": r["sharpe_composite"],
            "adx": r["adx"],
            "up_ratio_60": r["up_ratio_60"],
            "n_changes": r["n_changes"],
            "n_points": r["n_points"],
            "asof_date": asof_date,
        })
    return rows, cols


def refresh_data(base_url=DEFAULT_BASE, timeout=20):
    """从 CloudBase API 拉,重写本地 CSV"""
    t0 = datetime.now()
    try:
        list_data = fetch(f"{base_url}/list?top_n=500", timeout)
        hist_data = fetch(f"{base_url}/trend-history", timeout)
        items = list_data.get("items", [])
        asof = list_data.get("asof_date", "")
        cols = ["code", "name", "category", "strength_label", "fund_size_yi",
                "slope_20", "slope_50", "slope_120", "sharpe_composite", "adx",
                "up_ratio_60", "n_changes", "n_points", "asof_date"]
        # API items 缺字段兜底
        rows = []
        for it in items:
            r = dict(it)
            r.setdefault("fund_size_yi", 0)
            r.setdefault("category", "其他")
            r["asof_date"] = asof
            rows.append(r)
        write_csv(DATA_DIR / "results.csv", rows, cols)

        points = hist_data.get("points", [])
        hist_rows = []
        for it in hist_data.get("items", []):
            row = {"code": it["code"], "name": it["name"]}
            for p, v in zip(points, it.get("history", [])):
                row[p] = v
            hist_rows.append(row)
        write_csv(DATA_DIR / "etf_trend_history.csv", hist_rows, ["code", "name"] + points)
        # 用 sina K 线确认实际最新日期,避免 API 滞后
        try:
            sys.path.insert(0, str(Path(__file__).parent / "lib"))
            from algorithm import fetch_kline as _fk
            _k = _fk("510300", 250)  # 拿一只
            if _k is not None:
                _latest = _k["date"].iloc[-1]
                _ds = _latest if isinstance(_latest, str) else _latest.strftime("%Y%m%d")
                _ds = str(_ds).replace("-", "").replace("/", "")[:8]
                if _ds.isdigit():
                    asof = _ds  # 覆盖 API 陈旧的 asof
                    # rows 里也改 asof_date
                    for _r in rows:
                        _r["asof_date"] = asof
                    # 重写 results.csv
                    write_csv(DATA_DIR / "results.csv", rows, cols)
        except Exception:
            pass

        (DATA_DIR / ".asof").write_text(asof, encoding="utf-8")

        return {"ok": True, "asof_date": asof, "n_etfs": len(items), "n_points": len(points),
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "elapsed_ms": int((datetime.now() - t0).total_seconds() * 1000),
                "error": None, "mode": "api"}
    except Exception as e:
        return {"ok": False, "error": str(e), "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "asof_date": None, "n_etfs": 0, "n_points": 0,
                "elapsed_ms": int((datetime.now() - t0).total_seconds() * 1000),
                "mode": "api"}



def recompute_locally(codes=None, progress_cb=None):
    """用 v27 算法本地重算(慢,5-10min)

    Returns: dict (同 refresh_data 格式)
    """
    import pandas as pd
    sys.path.insert(0, str(Path(__file__).parent / "lib"))
    import algorithm as algo

    t0 = datetime.now()
    try:
        if codes is None:
            # 从项目内池文件读(不依赖外部路径)
            pool_path = DATA_DIR / "etf_pool.csv"
            if pool_path.exists():
                pool = pd.read_csv(pool_path, dtype={"代码": str})
                codes = pool["代码"].tolist()
            else:
                codes = []

        print(f"[recompute] {len(codes)} 只 ETF,启动 v27 算法...")
        metrics_df = algo.compute_all_metrics(codes, progress_callback=progress_cb)
        asof = datetime.now().strftime("%Y%m%d")
        rows, cols = _build_results_csv_from_metrics(metrics_df, asof)
        write_csv(DATA_DIR / "results.csv", rows, cols)
        (DATA_DIR / ".asof").write_text(asof, encoding="utf-8")

        # 趋势历史:本地不重新构建,保留 etf_trend_history.csv
        # 如果前端读到 n_points=0 表明没有历史,可以反查文件

        return {"ok": True, "asof_date": asof, "n_etfs": len(rows), "n_points": -1,  # -1 表示保留文件中的历史
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "elapsed_ms": int((datetime.now() - t0).total_seconds() * 1000),
                "error": None, "mode": "local"}
    except Exception as e:
        return {"ok": False, "error": str(e), "fetched_at": t0.isoformat(timespec="seconds"),
                "asof_date": None, "n_etfs": 0, "n_points": 0, "elapsed_ms": 0,
                "mode": "local"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default=DEFAULT_BASE)
    p.add_argument("--mode", choices=["api", "local"], default="api",
                   help="api=从 CloudBase 拉(快), local=v27 本地重算(慢)")
    args = p.parse_args()
    if args.mode == "api":
        res = refresh_data(args.base)
    else:
        res = recompute_locally()
    if res["ok"]:
        print(f"✅ mode={res['mode']} · {res['n_etfs']} ETFs · {res['elapsed_ms']}ms")
    else:
        print(f"❌ {res['error']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
