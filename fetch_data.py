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
import requests

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


def _load_pool_meta():
    """加载 ETF 池元数据(name_map, fund_size_map, cat_map),模块级缓存"""
    name_map = {}
    fund_size_map = {}
    cat_map = {}
    pool_path = DATA_DIR / "etf_pool.csv"
    cat_path = DATA_DIR / "industry_category.csv"
    try:
        if cat_path.exists():
            cat_df = pd.read_csv(cat_path, dtype={"代码": str})
            cat_map = dict(zip(cat_df["代码"], cat_df["行业分类"]))
    except Exception:
        pass
    try:
        if pool_path.exists():
            pool = pd.read_csv(pool_path, dtype={"代码": str})
            name_map = dict(zip(pool["代码"], pool["名称"]))
            fund_size_map = dict(zip(pool["代码"].astype(str), pool["fund_size_yi"].astype(float)))
            if not cat_map:
                cat_col = next((c for c in ["theme", "cluster", "category"] if c in pool.columns), None)
                cat_map = dict(zip(pool["代码"], pool[cat_col].astype(str) if cat_col else "其他"))
    except Exception:
        pass
    return name_map, fund_size_map, cat_map


def _build_results_csv_from_metrics(metrics_df, asof_date):
    """把算法输出转成 results.csv 格式"""
    name_map, fund_size_map, cat_map = _load_pool_meta()

    cols = ["code", "name", "category", "strength_label", "fund_size_yi",
            "latest_close", "latest_volume",
            "slope_20", "slope_50", "slope_120", "sharpe_composite", "adx",
            "up_ratio_60", "n_changes", "n_points", "asof_date"]
    rows = []
    for _, r in metrics_df.iterrows():
        code = str(r["code"]).zfill(6)
        rows.append({
            "code": code,
            "name": name_map.get(code, code),
            "category": cat_map.get(code, "其他"),
            "strength_label": r.get("strength_label", "横盘震荡"),
            "fund_size_yi": fund_size_map.get(code, 0),
            "latest_close": r.get("latest_close", 0),
            "latest_volume": r.get("latest_volume", 0),
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
                "latest_close", "latest_volume",
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
        # 用腾讯源 K 线确认实际最新日期,避免 API 滞后
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from lib import algorithm as algo_fix
            _k = algo_fix.fetch_kline_tencent("510300")
            if _k is not None:
                _latest = _k["date"].iloc[-1]
                _ds = _latest if isinstance(_latest, str) else _latest.strftime("%Y%m%d")
                _ds = str(_ds).replace("-", "").replace("/", "")[:8]
                if _ds.isdigit() and _ds > asof:
                    asof = _ds  # 覆盖 API 陈旧的 asof
                    for _r in rows:
                        _r["asof_date"] = asof
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
    """用 v27 算法本地重算 — 算当前分类 + 生成 25 天趋势历史

    流程:
      1. 从池文件读 codes (或直接传入)
      2. 预拉每只 K 线 275 天 (250+25),存为 cache
      3. 对每只 ETF:
         - 计算当前分类(最后 250 天)
         - 回看 25 天,每天往前移 1 个交易日重新分
         - 累计 25 个标签
      4. 写 results.csv + etf_trend_history.csv + .asof

    Returns: dict (同 refresh_data 格式)
    """
    import pandas as pd
    sys.path.insert(0, str(Path(__file__).parent))
    from lib import algorithm as algo

    t0 = datetime.now()
    try:
        if codes is None:
            pool_path = DATA_DIR / "etf_pool.csv"
            if pool_path.exists():
                pool = pd.read_csv(pool_path, dtype={"代码": str})
                codes = pool["代码"].tolist()
            else:
                codes = []
        name_map, _, _ = _load_pool_meta()

        total = len(codes)
        print(f"[recompute] {total} 只 ETF,启动 v27...")

        # 预拉所有 K 线(避免重复网络 I/O)
        kline_cache = {}
        for i, code in enumerate(codes):
            kw = algo.fetch_kline(code, min_len=250)
            # algo.fetch_kline 内部已有三源交叉验证(Sina+Tencent+163),无需单独降级
            if kw is not None and len(kw) >= 100:
                kw = kw.dropna(subset=["close"]).reset_index(drop=True)
                # 新 ETF(<250 天):前向 pad 到 250
                if len(kw) < 250:
                    pad_needed = 250 - len(kw)
                    first_row = kw.iloc[:1].copy()
                    pads = pd.concat([first_row] * pad_needed, ignore_index=True)
                    kw = pd.concat([pads, kw], ignore_index=True).reset_index(drop=True)
                kline_cache[code] = kw
            if progress_cb:
                progress_cb(i + 1, total, code, None, "kline")

        print(f"K 线缓存: {len(kline_cache)}/{total} 只")

        # 计算所有指标 + 25 天历史
        metrics_rows = []
        hist_rows = []
        # 确定 25 天日期列名
        # 从第一只 K 线获取日期区间
        first_k = list(kline_cache.values())[0] if kline_cache else None
        if first_k is not None:
            dates = pd.to_datetime(first_k["date"]).dt.strftime("%Y%m%d").tolist()
            # 取最后 25 个交易日作为历史点
            hist_dates = dates[-(25):] if len(dates) >= 25 else dates
        else:
            hist_dates = [f"d_{i}" for i in range(25)]

        done = 0
        for code in codes:
            kw = kline_cache.get(code)
            if kw is None:
                # 缺失 K 线 → 填 N/A,不跳过 — 保证 207 只不丢
                row = {"code": code, "category": name_map.get(code, code),
                       "strength_label": "N/A", "fund_size_yi": 0,
                       "slope_20": 0, "slope_50": 0, "slope_120": 0,
                       "sharpe_composite": 0, "adx": 0, "up_ratio_60": 0,
                       "n_changes": 0, "n_points": 25}
                metrics_rows.append(row)
                hist = {"code": code, "name": name_map.get(code, code)}
                for t, _d in enumerate(hist_dates):
                    hist[f"d_{_d}"] = "未知"
                hist_rows.append(hist)
                if progress_cb:
                    progress_cb(done + 1, total, code, None, "no kline")
                done += 1
                continue

            close = kw["close"].astype(float).values
            high = kw["high"].astype(float).values if "high" in kw.columns else close
            low = kw["low"].astype(float).values if "low" in kw.columns else close

            # 当前分类
            m = algo.calc_single_etf(kw)
            if m is None:
                if progress_cb:
                    progress_cb(done + 1, total, code, None, "calc fail")
                done += 1
                continue

            # 最新价 & 成交量
            latest_close = close[-1] if len(close) > 0 else 0.0
            latest_volume = kw["volume"].astype(float).values[-1] if "volume" in kw.columns and len(kw) > 0 else 0.0

            row = {"code": code, **m,
                   "latest_close": float(latest_close),
                   "latest_volume": float(latest_volume)}
            metrics_rows.append(row)

            # 回看 25 天 (批量优化: ~15x 加速)
            labels_25 = algo._compute_sliding_labels(
                close, high, low, n_windows=25
            )

            hist = {"code": code, "name": name_map.get(code, code)}
            for t, lbl in enumerate(labels_25):
                hist[f"d_{hist_dates[t]}"] = lbl if len(hist_dates) > t else lbl
            hist_rows.append(hist)

            done += 1
            if progress_cb:
                progress_cb(done, total, code, m, "ok")

        # asof 用 K 线最后日期而非执行时刻:从第一只(最完整)K线取
        asof = datetime.now().strftime("%Y%m%d")  # fallback
        if kline_cache:
            _first_k = list(kline_cache.values())[0]
            _last_date = _first_k["date"].iloc[-1]
            _ds = str(_last_date).replace("-", "").replace("/", "")[:8]
            if _ds.isdigit():
                asof = _ds
        metrics_df = pd.DataFrame(metrics_rows)
        rows, cols = _build_results_csv_from_metrics(metrics_df, asof)
        write_csv(DATA_DIR / "results.csv", rows, cols)

        # 写 trend_history — 列: code, name, d_20260529, ..., d_20260706
        if hist_rows:
            hist_cols = ["code", "name"] + [f"d_{d}" for d in hist_dates]
            write_csv(DATA_DIR / "etf_trend_history.csv", hist_rows, hist_cols)

        (DATA_DIR / ".asof").write_text(asof, encoding="utf-8")

        return {"ok": True, "asof_date": asof, "n_etfs": len(rows),
                "n_points": 25,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
                "elapsed_ms": int((datetime.now() - t0).total_seconds() * 1000),
                "error": None, "mode": "local"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"ok": False, "error": str(e),
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
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
