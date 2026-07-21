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


def _date_int_to_iso(d: str) -> str:
    """YYYYMMDD -> YYYY-MM-DD;原样穿透其他格式"""
    s = str(d).replace("-", "").replace("/", "")[:8]
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return str(d)


def _build_trend_history_from_klines(klines: dict, name_map: dict, n_days: int = 25):
    """用本地 K 线缓存重新生成趋势历史(最近 n_days 个交易日的标签)。

    返回:(hist_rows, hist_dates_iso)
      - hist_rows: List[dict],每个 key = f"d_{ISO_date}",value = 标签
      - hist_dates_iso: 最近 n_days 的 ISO 日期串 ["2026-07-04", ...]
    """
    import numpy as np
    sys.path.insert(0, str(Path(__file__).parent))
    from lib import algorithm as _algo

    if not klines:
        return [], []

    # 选 K 线最长的一只作为日期基准 (日期应对齐所有 ETF)
    pivot_code = max(klines.keys(), key=lambda c: len(klines[c]) if klines.get(c) is not None else 0)
    pivot_k = klines.get(pivot_code)
    if pivot_k is None or "date" not in pivot_k.columns or len(pivot_k) < n_days:
        return [], []

    dates = pd.to_datetime(pivot_k["date"]).dt.strftime("%Y-%m-%d").tolist()
    hist_dates = dates[-n_days:]

    rows = []
    for code, kw in klines.items():
        if kw is None or len(kw) < 20:
            row = {"code": str(code).zfill(6), "name": name_map.get(str(code).zfill(6), str(code))}
            for d in hist_dates:
                row[f"d_{d}"] = "未知"
            rows.append(row)
            continue

        close = kw["close"].astype(float).values
        high = kw["high"].astype(float).values if "high" in kw.columns else close
        low = kw["low"].astype(float).values if "low" in kw.columns else close

        try:
            labels_25 = _algo._compute_sliding_labels(close, high, low, n_windows=n_days)
        except Exception:
            labels_25 = ["横盘震荡"] * n_days

        # 取最后 n_days 个标签(可能不足,前置补位)
        if len(labels_25) < n_days:
            labels_25 = (["横盘震荡"] * (n_days - len(labels_25))) + list(labels_25)
        labels_25 = labels_25[-n_days:]

        code6 = str(code).zfill(6)
        row = {"code": code6, "name": name_map.get(code6, code6)}
        for d, lbl in zip(hist_dates, labels_25):
            row[f"d_{d}"] = lbl if lbl else "横盘震荡"
        rows.append(row)

    return rows, hist_dates


def _load_kline_cache_for_trend():
    """复用 recompute_locally 的 K 线 pickle 缓存;没有就跑一次最便宜的预热。

    返回 (klines_dict, name_map)。失败时返回空 dict,不抛。
    """
    import pickle
    from datetime import date as _date_cls
    sys.path.insert(0, str(Path(__file__).parent))
    from lib import algorithm as _algo
    from lib.safe_io import exclusive_lock, atomic_write_pickle

    cache_dir = DATA_DIR / ".kline_cache"
    cache_dir.mkdir(exist_ok=True)
    cache_key = _date_cls.today().strftime("%Y%m%d")
    cache_path = cache_dir / f"klines_{cache_key}.pkl"
    lock_path = cache_dir / f"klines_{cache_key}.lock"
    kline_cache = {}
    try:
        with exclusive_lock(lock_path, timeout=10):
            if cache_path.exists():
                try:
                    with cache_path.open("rb") as f:
                        kline_cache = pickle.load(f)
                except Exception:
                    kline_cache = {}

            # 决定要现拉的 ETF 列表(从 etf_pool.csv)
            pool_path = DATA_DIR / "etf_pool.csv"
            if pool_path.exists():
                pool = pd.read_csv(pool_path, dtype={"代码": str})
                codes = pool["代码"].tolist()
            else:
                codes = list(kline_cache.keys())

            to_fetch = [c for c in codes if c not in kline_cache or kline_cache.get(c) is None]
            if to_fetch:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                with ThreadPoolExecutor(max_workers=10) as pool:
                    fut_map = {pool.submit(_algo.fetch_kline, c, 250): c for c in to_fetch}
                    for fut in as_completed(fut_map):
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

            # 落盘(就算空也好,标个 mtime,避免下次误判)
            try:
                atomic_write_pickle(cache_path, kline_cache)
            except Exception:
                pass
    except TimeoutError:
        # 取锁失败:用空 dict 继续(后续会现拉,不会卡死主流程)
        pass

    name_map, _, _ = _load_pool_meta()
    return kline_cache, name_map


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
            "latest_close", "latest_volume", "latest_amount", "change_pct",  # 2026-07-20 补: 当日涨跌幅
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
            "change_pct": r.get("change_pct", 0),  # 2026-07-20 补: 当日涨跌幅
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


def _parse_volume_from_fields(fields):
    """从腾讯源 fields 数组里提取成交量(手)
    
    字段位置（实测）:
      - fields[6]:  累计成交量（手）
      - fields[37]: 累计成交量（备用，跨品种冗余）
    """
    for idx in (6, 37):
        if len(fields) > idx:
            try:
                v = float(fields[idx])
                if v > 0:
                    return v
            except (ValueError, IndexError):
                continue
    return None


def _batch_fetch_latest_from_tencent(codes: list, max_workers=10):
    """批量从腾讯源拉最新价+成交额,返回 {code: (close, amount)} 字典

    修复: 旧实现按 fields 位置(fields[35] / fields[3])解析,腾讯跨品种(尤其债券/跨境 ETF)
    字段顺序不一致,会把昨收/价格当成成交额。新实现:
      - 最新价: 走腾讯快照 fields[3] (稳定)
      - 成交额: 复用 lib.algorithm.fetch_amount 的正则解析(跨品种鲁棒,且自带 sh/sz 双前缀兜底)
    """
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from lib.algorithm import tencent_market_prefix, _parse_amount_from_text
    # 复用模块级 _SESSION(共享 TCP 连接),保证并发时连接池不爆
    from lib.algorithm import _SESSION as _algo_session

    def _fetch_one(code):
        prefix = tencent_market_prefix(code)
        url = f"https://web.sqt.gtimg.cn/q={prefix}{code}"
        try:
            raw = _algo_session.get(url, timeout=5)
            if raw.status_code != 200:
                return code, (None, None)
            text = raw.text
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
            # 成交额: 用 algorithm 模块的统一正则(跨品种鲁棒),失败再试反前缀
            amount_val = _parse_amount_from_text(text)
            if amount_val is None:
                alt = "sz" if prefix == "sh" else "sh"
                try:
                    raw2 = _algo_session.get(f"https://web.sqt.gtimg.cn/q={alt}{code}", timeout=5)
                    if raw2.status_code == 200:
                        amount_val = _parse_amount_from_text(raw2.text)
                except Exception:
                    pass
            # 成交量(手): fields[6] 或 fields[37]（腾讯源冗余设计）
            volume_val = _parse_volume_from_fields(fields)
            return code, (close_val, amount_val, volume_val)
        except Exception:
            return code, (None, None)

    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for code, (close_val, amount_val, volume_val) in pool.map(_fetch_one, codes):
            results[code] = (close_val, amount_val, volume_val)
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
            # 显式初始化价格字段（保证 row 里始终有这些 key）
            r.setdefault("latest_close", 0)
            r.setdefault("latest_volume", 0)
            r.setdefault("latest_amount", 0)
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
                close_val, amount_val, volume_val = tencent_prices[code]
                if close_val is not None:
                    r["latest_close"] = close_val
                if amount_val is not None:
                    r["latest_amount"] = amount_val
                if volume_val is not None:
                    r["latest_volume"] = volume_val

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

        # === 【关键修复】用本地 K 线重新生成趋势历史 (同步,使用真实日期) ===
        # 旧 bug: API 端 /trend-history 返回的 dates 滞后,导致趋势演变列停在旧日期;
        #         之前依赖后台 recompute 异步补,但常常失败/被忽略,导致用户刷新后仍看到旧日期。
        # 这里直接同步生成 trend history 的"真实日期版本",保证 asof 与趋势演变日期一致。
        try:
            klines, name_map = _load_kline_cache_for_trend()
            hist_rows_local, hist_dates_local = _build_trend_history_from_klines(klines, name_map, n_days=25)
            if hist_rows_local and hist_dates_local:
                hist_cols = ["code", "name"] + [f"d_{d}" for d in hist_dates_local]
                write_csv(DATA_DIR / "etf_trend_history.csv", hist_rows_local, hist_cols)
                points = [f"d_{d}" for d in hist_dates_local]
        except Exception as _e_trend:
            # 失败不阻断主流程,保留 API 版
            print(f"[refresh_data] 本地趋势历史生成失败,保留 API 版本: {_e_trend}")

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



def _load_codes_and_pool_meta(codes):
    """2026-07-21 γ' 靶点: 加载 codes + name_map (从 etf_pool.csv 或外部传入)

    Returns:
        (codes, name_map) — codes 是 list[str],name_map 是 dict[code, name]
    """
    import pandas as pd
    if codes is None:
        pool_path = DATA_DIR / "etf_pool.csv"
        if pool_path.exists():
            pool = pd.read_csv(pool_path, dtype={"代码": str})
            codes = pool["代码"].tolist()
        else:
            codes = []
    name_map, _, _ = _load_pool_meta()
    return codes, name_map


def _fetch_klines_with_cache(codes, total, progress_cb):
    """2026-07-21 γ' 靶点: 带 pickle 缓存的 K 线拉取 (复用昨日 + 并行补拉今日缺失)

    Returns:
        kline_cache: dict[code, DataFrame]
    """
    import pickle
    from datetime import date as _date_cls
    from lib.safe_io import exclusive_lock, atomic_write_pickle

    cache_dir = DATA_DIR / ".kline_cache"
    cache_dir.mkdir(exist_ok=True)
    cache_key = _date_cls.today().strftime("%Y%m%d")
    cache_path = cache_dir / f"klines_{cache_key}.pkl"
    lock_path = cache_dir / f"klines_{cache_key}.lock"
    kline_cache = {}

    try:
        with exclusive_lock(lock_path, timeout=10):
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
            # 拉到一部分就立刻原子写回 pickle(锁内),下一个进程能立刻用上
            try:
                atomic_write_pickle(cache_path, kline_cache)
            except Exception as e:
                print(f"[K线缓存] 中间落盘失败(下次重拉): {e}")
    except TimeoutError:
        print("[K线缓存] 锁等待超时,跳过本轮,沿用空缓存继续")
        kline_cache = {}
    return kline_cache


def _pick_hist_dates(kline_cache):
    """2026-07-21 γ' 靶点: 从最完整的 K 线选最后 25 天作为历史点列名

    Returns:
        list[str] — 25 个 YYYYMMDD 日期字符串
    """
    if not kline_cache:
        return [f"d_{i}" for i in range(25)]
    pivot_code = max(kline_cache.keys(),
                     key=lambda c: len(kline_cache[c]) if kline_cache.get(c) is not None else 0)
    first_k = kline_cache.get(pivot_code)
    if first_k is None or "date" not in first_k.columns:
        return [f"d_{i}" for i in range(25)]
    dates = pd.to_datetime(first_k["date"]).dt.strftime("%Y%m%d").tolist()
    return dates[-(25):] if len(dates) >= 25 else dates


def _build_metric_row_no_kline(code, name_map):
    """2026-07-21 γ' 靶点: K 线缺失时填 N/A 指标行 (保证 207 只不丢)"""
    return {
        "code": code, "category": name_map.get(code, code),
        "strength_label": "N/A", "fund_size_yi": 0,
        "slope_20": 0, "slope_50": 0, "slope_120": 0,
        "sharpe_composite": 0, "adx": 0, "up_ratio_60": 0,
        "n_changes": 0, "n_points": 25,
        "change_pct": 0,
    }


def _build_metric_row(code, kw, m):
    """2026-07-21 γ' 靶点: 单只 ETF 的指标行 (从 K 线 + 算法结果构建)

    Returns:
        dict — 单只 ETF 的 results.csv 行
    """
    close = kw["close"].astype(float).values
    latest_close = close[-1] if len(close) > 0 else 0.0
    latest_volume = kw["volume"].astype(float).values[-1] if "volume" in kw.columns and len(kw) > 0 else 0.0
    if len(close) >= 2 and close[-2] > 0:
        change_pct = float((close[-1] - close[-2]) / close[-2] * 100)
    else:
        change_pct = 0.0
    latest_amount = algo.fetch_amount(code)
    return {"code": code, **m,
            "latest_close": float(latest_close),
            "latest_volume": float(latest_volume),
            "latest_amount": float(latest_amount) if latest_amount else 0.0,
            "change_pct": change_pct}


def _compute_metrics_and_history(codes, kline_cache, name_map, hist_dates, progress_cb):
    """2026-07-21 γ' 靶点: 计算所有指标 + 25 天历史 (核心算法部分)

    Returns:
        (metrics_rows, hist_rows) — 都是 list[dict]
    """
    metrics_rows = []
    hist_rows = []
    done = 0
    for code in codes:
        kw = kline_cache.get(code)
        if kw is None:
            metrics_rows.append(_build_metric_row_no_kline(code, name_map))
            hist = {"code": code, "name": name_map.get(code, code)}
            for t, _d in enumerate(hist_dates):
                hist[f"d_{_d}"] = "未知"
            hist_rows.append(hist)
            if progress_cb:
                progress_cb(done + 1, len(codes), code, None, "no kline")
            done += 1
            continue

        m = algo.calc_single_etf(kw)
        if m is None:
            if progress_cb:
                progress_cb(done + 1, len(codes), code, None, "calc fail")
            done += 1
            continue

        # 指标行
        metrics_rows.append(_build_metric_row(code, kw, m))

        # 25 天滑动标签
        close = kw["close"].astype(float).values
        high = kw["high"].astype(float).values if "high" in kw.columns else close
        low = kw["low"].astype(float).values if "low" in kw.columns else close
        labels_25 = algo._compute_sliding_labels(close, high, low, n_windows=25)

        hist = {"code": code, "name": name_map.get(code, code)}
        for t, lbl in enumerate(labels_25):
            hist[f"d_{hist_dates[t]}"] = lbl if len(hist_dates) > t else lbl
        hist_rows.append(hist)

        done += 1
        if progress_cb:
            progress_cb(done, len(codes), code, m, "ok")
    return metrics_rows, hist_rows


def _pick_asof_from_cache(kline_cache):
    """2026-07-21 γ' 靶点: 从 K 线最后日期取 asof (而非执行时刻)"""
    asof = datetime.now().strftime("%Y%m%d")
    if not kline_cache:
        return asof
    _first_k = list(kline_cache.values())[0]
    _last_date = _first_k["date"].iloc[-1]
    _ds = str(_last_date).replace("-", "").replace("/", "")[:8]
    if _ds.isdigit():
        asof = _ds
    return asof


def _persist_results_and_history(metrics_df, hist_rows, hist_dates, asof, cache_key=None):
    """2026-07-21 γ' 靶点: 落盘 results.csv + etf_trend_history.csv + .asof

    Returns:
        (n_rows, ok) — n_rows 是 results.csv 行数
    """
    rows, cols = _build_results_csv_from_metrics(metrics_df, asof)
    write_csv(DATA_DIR / "results.csv", rows, cols)

    if hist_rows:
        hist_cols = ["code", "name"] + [f"d_{d}" for d in hist_dates]
        write_csv(DATA_DIR / "etf_trend_history.csv", hist_rows, hist_cols)

    (DATA_DIR / ".asof").write_text(asof, encoding="utf-8")
    return len(rows), True


def recompute_locally(codes=None, progress_cb=None):
    """用 v27 算法本地重算 — 算当前分类 + 生成 25 天趋势历史 (2026-07-21 γ' 靶点: 215 → 25L 调度器)

    流程 (六阶段调度):
      1. _load_codes_and_pool_meta       加载 codes + name_map
      2. _fetch_klines_with_cache         pickle 缓存 + 并行拉 K 线
      3. _pick_hist_dates                  从最长 K 线取最后 25 天日期
      4. _compute_metrics_and_history     算所有 ETF 的指标 + 25 天历史
      5. _pick_asof_from_cache             asof 用 K 线最后日期
      6. _persist_results_and_history      落盘 results.csv + trend_history.csv + .asof

    Returns: dict (同 refresh_data 格式)
    """
    import pandas as pd
    sys.path.insert(0, str(Path(__file__).parent))
    from lib import algorithm as algo

    t0 = datetime.now()
    try:
        codes, name_map = _load_codes_and_pool_meta(codes)
        total = len(codes)
        print(f"[recompute] {total} 只 ETF,启动 v27...")

        kline_cache = _fetch_klines_with_cache(codes, total, progress_cb)
        hist_dates = _pick_hist_dates(kline_cache)

        metrics_rows, hist_rows = _compute_metrics_and_history(
            codes, kline_cache, name_map, hist_dates, progress_cb
        )

        asof = _pick_asof_from_cache(kline_cache)
        metrics_df = pd.DataFrame(metrics_rows)
        n_rows, _ = _persist_results_and_history(metrics_df, hist_rows, hist_dates, asof)

        return {"ok": True, "asof_date": asof, "n_etfs": n_rows,
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


def _check_cloudbase_health(timeout=3):
    """2026-07-21 A: 检查 CloudBase API 是否可达 (为 streamlit banner 提供状态)

    轻量 GET 请求, 3s timeout (避免阻塞启动)
    Returns:
        dict: {"ok": bool, "latency_ms": int, "error": str or None, "checked_at": ISO str}
    """
    t0 = datetime.now()
    try:
        req = urllib.request.Request(
            DEFAULT_BASE + "?asof_date=" + datetime.now().strftime("%Y%m%d"),
            headers={"User-Agent": "yangyang-health/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(64)  # 读少量数据即可
        latency = int((datetime.now() - t0).total_seconds() * 1000)
        return {"ok": True, "latency_ms": latency, "error": None,
                "checked_at": datetime.now().isoformat(timespec="seconds")}
    except Exception as e:
        latency = int((datetime.now() - t0).total_seconds() * 1000)
        return {"ok": False, "latency_ms": latency, "error": str(e)[:100],
                "checked_at": datetime.now().isoformat(timespec="seconds")}


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
