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
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

from lib.constants import classify_name

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
            "latest_close", "latest_volume", "latest_amount",
            "slope_20", "slope_50", "slope_120", "sharpe_composite", "adx",
            "up_ratio_60", "n_changes", "n_points", "asof_date"]
    rows = []
    for _, r in metrics_df.iterrows():
        code = str(r["code"]).zfill(6)
        rows.append({
            "code": code,
            "name": name_map.get(code, code),
            "category": (cat_map.get(code) if cat_map.get(code) else classify_name(name_map.get(code, code))) or "其他",
            "strength_label": r.get("strength_label", "横盘震荡"),
            "fund_size_yi": fund_size_map.get(code, 0) or 0,
            "latest_close": r.get("latest_close", 0),
            "latest_volume": r.get("latest_volume", 0),
            "latest_amount": r.get("latest_amount", 0),
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


def _batch_fetch_latest_from_tencent(codes: list, max_workers=10):
    """批量从腾讯源拉最新价+成交额，返回 {code: (close, amount)} 字典

    腾讯快照 fields[3] = 最新价, fields[35] = 最新价/成交量/成交额
    """
    from lib.algorithm import _tencent_market_prefix

    def _fetch_one(code):
        prefix = _tencent_market_prefix(code)
        url = f"https://web.sqt.gtimg.cn/q={prefix}{code}"
        try:
            raw = urllib.request.urlopen(url, timeout=5).read()
            text = raw.decode("gbk", errors="replace")
            if not text or "~" not in text:
                return code, (None, None)
            fields = text.split("~")
            close_val = None
            if len(fields) > 3:
                try:
                    c = float(fields[3])
                    if c > 0:
                        close_val = c
                except (ValueError, IndexError):
                    pass
            amount_val = None
            if len(fields) > 35 and "/" in fields[35]:
                parts = fields[35].split("/")
                if len(parts) >= 3:
                    try:
                        a = float(parts[2])
                        if a > 0:
                            amount_val = a
                    except (ValueError, IndexError):
                        pass
            return code, (close_val, amount_val)
        except Exception:
            return code, (None, None)

    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for code, (close_val, amount_val) in pool.map(_fetch_one, codes):
            results[code] = (close_val, amount_val)
    return results


def refresh_data(base_url=DEFAULT_BASE, timeout=20):
    """从 CloudBase API 拉,重写本地 CSV

    流程：
      1. 调 /list + /trend-history （迅速拿到 trend + 分类）
      2. 批量从腾讯源补最新价 + 成交额（单只 ~0.05s，207 只 ~1s）
      3. 写 results.csv + etf_trend_history.csv

    总耗时通常 ≤ 10s，不会触达 Streamlit Cloud 60s 超时。
    无 latest_close = 以腾讯实时报价填写（无则保留 0）。
    """
    t0 = datetime.now()
    try:
        list_data = fetch(f"{base_url}/list?top_n=500", timeout)
        hist_data = fetch(f"{base_url}/trend-history", timeout)
        items = list_data.get("items", [])
        asof = list_data.get("asof_date", "")
        cols = ["code", "name", "category", "strength_label", "fund_size_yi",
                "latest_close", "latest_volume", "latest_amount",
                "slope_20", "slope_50", "slope_120", "sharpe_composite", "adx",
                "up_ratio_60", "n_changes", "n_points", "asof_date"]
        # API items 缺字段兜底
        rows = []
        codes_needing_price = []
        for it in items:
            r = dict(it)
            r.setdefault("fund_size_yi", 0)
            r.setdefault("category", "其他")
            r["asof_date"] = asof
            rows.append(r)
            # 记录 API 最新价 / 成交额全为 0 的，后续补腾讯源
            has_price = float(r.get("latest_close", 0) or 0) > 0
            if not has_price:
                codes_needing_price.append(r["code"])

        # 写基础 CSV（API 原始数据）
        write_csv(DATA_DIR / "results.csv", rows, cols)

        points = hist_data.get("points", [])
        hist_rows = []
        for it in hist_data.get("items", []):
            row = {"code": it["code"], "name": it["name"]}
            for p, v in zip(points, it.get("history", [])):
                row[p] = v
            hist_rows.append(row)
        write_csv(DATA_DIR / "etf_trend_history.csv", hist_rows, ["code", "name"] + points)

        # 用腾讯源补最新价：只补 API 全为 0 的代码（≈全部）
        tencent_prices = {}
        if codes_needing_price:
            try:
                tencent_prices = _batch_fetch_latest_from_tencent(codes_needing_price)
            except Exception:
                pass

        # 回填 latest_close / latest_amount
        for r in rows:
            code = r["code"]
            if code in tencent_prices:
                close_val, amount_val = tencent_prices[code]
                if close_val is not None:
                    r["latest_close"] = close_val
                if amount_val is not None:
                    r["latest_amount"] = amount_val

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
                    asof = _ds
                    for _r in rows:
                        _r["asof_date"] = asof
        except Exception:
            pass

        # 补全后再写一次 CSV（有腾讯回填的最新价 / 成交额）
        write_csv(DATA_DIR / "results.csv", rows, cols)

        if not asof:
            asof = datetime.now().strftime("%Y%m%d")
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

        # === Pickle 缓存 — 同一个交易日复用昨天拉好的 207 只 K 线,
        # 避免每天重拉全部（云环境被腾讯限流的风险也降低） ===
        import pickle
        from datetime import date as _date_cls
        cache_dir = DATA_DIR / ".kline_cache"
        cache_dir.mkdir(exist_ok=True)
        cache_key = _date_cls.today().strftime("%Y%m%d")
        cache_path = cache_dir / f"klines_{cache_key}.pkl"
        kline_cache = {}
        if cache_path.exists():
            try:
                with cache_path.open("rb") as f:
                    kline_cache = pickle.load(f)
                print(f"[K线缓存] 命中 {cache_key},复用 {len(kline_cache)} 只")
            except Exception as e:
                print(f"[K线缓存] 读取失败,重新拉: {e}")
                kline_cache = {}

        # 哪些没缓存到 · 还要现拉
        to_fetch = [c for c in codes if c not in kline_cache or kline_cache[c] is None]
        if to_fetch:
            print(f"[K线] 需重拉 {len(to_fetch)} 只")

        # 并行拉 K 线(IO 密集,多线程加速)
        fetched = {}
        with ThreadPoolExecutor(max_workers=10) as pool:
            fut_map = {pool.submit(algo.fetch_kline, code, 250): code for code in to_fetch}
            for i, fut in enumerate(as_completed(fut_map)):
                code = fut_map[fut]
                try:
                    kw = fut.result()
                except Exception:
                    kw = None
                if kw is not None and len(kw) >= 100:
                    kw = kw.dropna(subset=["close"]).reset_index(drop=True)
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

            # 从腾讯快照拉成交额
            latest_amount = algo.fetch_amount(code)
            row = {"code": code, **m,
                   "latest_close": float(latest_close),
                   "latest_volume": float(latest_volume),
                   "latest_amount": float(latest_amount) if latest_amount else 0.0}
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
        # 迭代完后同一日 kline_cache 持久化到 pickle,
        # 次日 refresh 只需拉未命中的部分
        try:
            with cache_path.open("wb") as f:
                pickle.dump(kline_cache, f)
            print(f"[K线缓存] 已落盘 {cache_key}: {len(kline_cache)} 只")
        except Exception as e:
            print(f"[K线缓存] 落盘失败(下次重拉): {e}")

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
