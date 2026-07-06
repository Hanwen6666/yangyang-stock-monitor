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
    # 合并 category(从 ETF 基础信息取,这里简化:从默认池文件读)
    pool_path = Path("/home/ubuntu/.openclaw/workspace/etf_strong_weak/etf_final_207.csv")
    cat_map = {}
    name_map = {}
    fund_size_map = {}
    if pool_path.exists():
        pool = pd.read_csv(pool_path) if 'pd' in dir() else None
    # 简化:用 etf_strong_weak CSV 补全
    try:
        import pandas as _pd
        pool = _pd.read_csv(pool_path, dtype={"代码": str})
        cat_map = dict(zip(pool["代码"], pool["theme"] if "theme" in pool.columns else pool.get("category", "其他")))
        name_map = dict(zip(pool["代码"], pool["名称"]))
        fund_size_map = dict(zip(pool["代码"], pool["fund_size_yi"]))
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
    except Exception as e:
        return {"ok": False, "error": str(e), "fetched_at": t0.isoformat(timespec="seconds"),
                "asof_date": None, "n_etfs": 0, "n_points": 0, "elapsed_ms": 0}

    items = list_data.get("items", [])
    asof = list_data.get("asof_date", "")
    cols = ["code", "name", "category", "strength_label", "fund_size_yi",
            "slope_20", "slope_50", "slope_120", "sharpe_composite", "adx",
            "up_ratio_60", "n_changes", "n_points", "asof_date"]
    write_csv(DATA_DIR / "results.csv",
              [{**it, "asof_date": asof} for it in items], cols)

    points = hist_data.get("points", [])
    hist_rows = []
    for it in hist_data.get("items", []):
        row = {"code": it["code"], "name": it["name"]}
        for p, v in zip(points, it.get("history", [])):
            row[p] = v
        hist_rows.append(row)
    write_csv(DATA_DIR / "etf_trend_history.csv", hist_rows, ["code", "name"] + points)
    (DATA_DIR / ".asof").write_text(asof, encoding="utf-8")

    return {"ok": True, "asof_date": asof, "n_etfs": len(items), "n_points": len(points),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "elapsed_ms": int((datetime.now() - t0).total_seconds() * 1000),
            "error": None, "mode": "api"}


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
            # 从池文件读
            pool_path = Path("/home/ubuntu/.openclaw/workspace/etf_strong_weak/etf_final_207.csv")
            pool = pd.read_csv(pool_path, dtype={"代码": str})
            codes = pool["代码"].tolist()

        print(f"[recompute] {len(codes)} 只 ETF,启动 v27 算法...")
        metrics_df = algo.compute_all_metrics(codes, progress_callback=progress_cb)
        asof = datetime.now().strftime("%Y%m%d")
        rows, cols = _build_results_csv_from_metrics(metrics_df, asof)
        write_csv(DATA_DIR / "results.csv", rows, cols)

        # 趋势历史:基于本次的 metrics 简单生成(只有 1 个时间点,前 25 天无法重算)
        # 这里复用 API 的 history(因为本地拉不到 25 天历史 K 线)
        # 也可以留空让前端显示空
        (DATA_DIR / ".asof").write_text(asof, encoding="utf-8")

        return {"ok": True, "asof_date": asof, "n_etfs": len(rows), "n_points": 0,
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
