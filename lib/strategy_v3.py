"""
强势股轮动策略 v3.0 — 算法核心

参考: strategy_manual.md (强势股轮动策略 v3.0)

核心模块:
  - load_local_klines: 从本地 stock_kline/ JSON 目录加载 2019 至今全 A K 线
  - MarketRegime: 大盘状态判定(强势/阶段底/横盘/弱势)
  - calc_score: 个股 5/10/20 日 α 三因子打分
  - run_backtest: 7 年回测主循环
  - generate_daily_orders: 当日调仓清单(明日操作)

数据源:
  - K 线:  ~/.openclaw/workspace/scripts/market_strength/data/stock_kline/
           sh_600000.json / sz_000001.json / bj_920000.json  共 5672 个文件
           每个 {date_str: {close, open, high, low, volume, pctChg}}
           时间范围: 2019-01-02 ~ 2026-07-10
  - 创业板指: index_399006.json (2010-06 起)
  - 股票池:  all_stocks_v2.json (5202 个 sz.000001 格式)
  - 名称:    stock_name_map.json (5530 条,code6 -> 中文名)

性能:
  - 本地 JSON 加载 1 次约 5-10s(5672 个文件)
  - 一次性 load 进内存后, 跑 7 年回测约 3-10s
  - 日常使用按 asof 切片即可
"""
from __future__ import annotations

import json
import math
import pickle
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import requests

# ============================================================
# 路径常量
# ============================================================
# 项目内的 stock_kline 数据源(全 A 2019 至今)
import os

# 2026-07-21 阶段 2 靶点 P1: hardcoded 路径加 YY_EXTERNAL_DATA_DIR 环境变量覆盖
# 默认保留现状, 老大可 set YY_EXTERNAL_DATA_DIR=/data 跨部署
_DEFAULT_EXTERNAL = "/home/ubuntu/.openclaw/workspace/scripts/market_strength/data"
EXTERNAL_DATA_DIR = Path(os.environ.get("YY_EXTERNAL_DATA_DIR", _DEFAULT_EXTERNAL))
STOCK_KLINE_DIR = EXTERNAL_DATA_DIR / "stock_kline"
INDEX_399006_PATH = EXTERNAL_DATA_DIR / "index_399006.json"
ALL_STOCKS_PATH = EXTERNAL_DATA_DIR / "all_stocks_v2.json"
NAME_MAP_PATH = EXTERNAL_DATA_DIR / "stock_name_map.json"
# 本项目内的 K 线回退目录(防止外部数据不可用)
INTERNAL_DATA_DIR = Path(__file__).parent.parent / "data" / ".a_kline_cache"

# ============================================================
# 参数(说明书附录 B)
# ============================================================
BENCHMARK_CODE = "399006"          # 创业板指
POOL_TOP_N = 30                   # 等权持仓数量
SCORE_THRESHOLD = 60              # 入场分数门槛
SCORE_THRESHOLD_BOTTOM = 50       # 阶段底期门槛
STOP_LOSS_DRAWDOWN = 0.10         # 10% 回撤止损
LOSER_DAYS = 10                   # 连续 10 日累计 α < 0 退出
PHASE_BOTTOM_WINDOW = 7           # 阶段底维持天数
MARKET_MIN_DURATION = 5           # 状态最小持续天数
EARLY_BOTTOM_BONUS = 15           # 见底加成分数
MA_FILTER = 200                   # MA200 过滤

# 仓位
POSITION_TABLE = {
    "强势": 1.00,
    "阶段底": 1.00,
    "横盘": 0.60,
    "弱势": 0.40,
}

# 三因子权重
W_5D = 0.40
W_10D = 0.20
W_20D = 0.40

# 交易成本
SLIPPAGE = 0.001          # 单边滑点 0.1%
STAMP_TAX_SELL = 0.001    # 印花税(卖出)
COMMISSION = 0.00025      # 佣金双边 0.025%

# 上市天数过滤
MIN_LISTING_DAYS = 250    # 至少 250 个交易日历史


# ============================================================
# HTTP — 复用 lib.algorithm 的 session
# ============================================================
# 2026-07-21 E5 迁移: tencent_market_prefix 迁到 lib.market_data (fetcher 单点权威)
from lib.market_data import tencent_market_prefix  # noqa: F401


# C4 加固: 错误计数器 + throttled warning (避免每只股票都打一行刷屏)
_tencent_error_count = {"param_error": 0, "http_error": 0, "network_error": 0, "empty_data": 0, "ok": 0}
_last_warn_at = {"param_error": 0.0, "http_error": 0.0}
_WARN_THROTTLE_S = 30  # 同类错误最少间隔 30s 才重复打


def _tencent_warn(category: str, msg: str):
    """throttled warning: 同类错误 30s 内只打一次, 累计计数"""
    import sys as _sys
    import time as _time
    now = _time.time()
    last = _last_warn_at.get(category, 0.0)
    if now - last < _WARN_THROTTLE_S:
        return  # throttled
    _last_warn_at[category] = now
    print(f"[tencent] ⚠️ {msg} (累计: {_tencent_error_count.get(category, 0)})", file=_sys.stderr, flush=True)


def _tencent_kline_one(code6: str, n: int = 250) -> pd.DataFrame | None:
    """拉单只 K 线(腾讯源,前复权)

    ⚠️ 2026-07-19 Bug Fix:
      旧实现用 f"{prefix}{code6}" 拼成 'shsh600519' (前缀重复),
      腾讯返回 'param error', 所有股票静默拉空.
      修正: code6 已是 6 位数字 (如 '600519'), prefix + code6 即可.

    C4 加固 (2026-07-19): 永久错误不再静默, 打 throttled warning + 累计计数,
    silent failure 变 loud failure, 便于排查.
    """
    prefix = tencent_market_prefix(code6)
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    code_param = f"{prefix}{code6}"  # 不再重复加 prefix: 'sh' + '600519' = 'sh600519'
    try:
        r = requests.get(
            url, params={"param": f"{code_param},day,,,{n},qfq"}, timeout=8
        )
        if r.status_code != 200:
            _tencent_error_count["http_error"] += 1
            _tencent_warn("http_error", f"{code6}: HTTP {r.status_code}")
            return None
        resp_json = r.json()
        if resp_json.get("code") != 0:
            _tencent_error_count["param_error"] += 1
            _tencent_warn("param_error", f"{code6}: 腾讯返回 code={resp_json.get('code')} msg={resp_json.get('msg', '')[:40]!r}")
            return None
        data = resp_json.get("data", {}) or {}
        msg = resp_json.get("msg", "")
        # ⚠️ 腾讯 隐式错误: code=0 但 msg="param error" 且 data=[] (n 超限/代码不存在)
        if msg and ("param error" in msg.lower() or "bad params" in msg.lower()):
            _tencent_error_count["param_error"] += 1
            _tencent_warn("param_error", f"{code6}: 腾讯 msg={msg!r} (代码可能不存在/n 超限)")
            return None
        if not isinstance(data, dict):
            # 腾讯错误响应可能 data=[] (list) 或其他非 dict
            _tencent_error_count["param_error"] += 1
            _tencent_warn("param_error", f"{code6}: 腾讯 data 非 dict ({type(data).__name__})")
            return None
        data = data.get(code_param, {})
        klines = data.get("qfqday") or data.get("day") or []
        if not klines:
            _tencent_error_count["empty_data"] += 1
            _tencent_warn("empty_data", f"{code6}: 腾讯返回空 K 线 (可能是退市/停牌)")
            return None
        rows = [
            {
                "date": k[0],
                "open": float(k[1]),
                "close": float(k[2]),
                "high": float(k[3]),
                "low": float(k[4]),
                "volume": float(k[5]) if len(k) > 5 else 0.0,
            }
            for k in klines
        ]
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        _tencent_error_count["ok"] += 1
        return df
    except Exception as e:
        _tencent_error_count["network_error"] += 1
        _tencent_warn("network_error", f"{code6}: {type(e).__name__}: {str(e)[:50]}")
        return None


def get_tencent_error_stats() -> dict:
    """返回累计错误计数 (供外部监测 / health check 用)"""
    return dict(_tencent_error_count)


def _tencent_batch_klines(codes: list, n: int = 250, max_workers: int = 20,
                           progress_cb: Callable | None = None) -> dict:
    """并发批量拉 K 线"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    out: dict[str, pd.DataFrame | None] = {}
    total = len(codes)
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        fut_map = {pool.submit(_tencent_kline_one, c, n): c for c in codes}
        for fut in as_completed(fut_map):
            code = fut_map[fut]
            try:
                out[code] = fut.result()
            except Exception:
                out[code] = None
            done += 1
            if progress_cb and done % 50 == 0:
                progress_cb(done, total, code, None, "kline")
    if progress_cb:
        progress_cb(total, total, "", None, "kline")
    return out


# ============================================================
# 标的池
# ============================================================
def get_a_stock_pool() -> pd.DataFrame:
    """获取全 A 标的池: 剔北交所 + 科创板

    优先从外部 all_stocks_v2.json + name_map 读;
    退化到本项目内 a_stock_pool.csv; 再退化到 akshare 拉取。

    返回 DataFrame: code, name (6 位代码, 已剔 688/4/8 开头)
    """
    cache_path = INTERNAL_DATA_DIR.parent / "a_stock_pool.csv"
    if cache_path.exists():
        try:
            df = pd.read_csv(cache_path, dtype={"code": str})
            df["code"] = df["code"].str.zfill(6)
            return df
        except Exception:
            pass

    # 优先: 外部 all_stocks_v2.json + name_map
    if ALL_STOCKS_PATH.exists() and NAME_MAP_PATH.exists():
        try:
            with ALL_STOCKS_PATH.open() as f:
                raw = json.load(f)
            with NAME_MAP_PATH.open() as f:
                name_map = json.load(f)
            rows = []
            for item in raw:
                # item 格式: "sz.000001" 或 "sh.600000"
                if "." not in item:
                    continue
                _prefix, code = item.split(".", 1)
                if len(code) != 6:
                    continue
                if code.startswith(("4", "8", "688")):
                    continue
                rows.append({"code": code, "name": name_map.get(code, code)})
            df = pd.DataFrame(rows).drop_duplicates("code").reset_index(drop=True)
            cache_path.parent.mkdir(exist_ok=True, parents=True)
            df.to_csv(cache_path, index=False, encoding="utf-8")
            return df
        except Exception:
            pass

    # 退化: akshare
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        df["code"] = df["code"].astype(str).str.zfill(6)
        df["name"] = df["name"].astype(str)
        mask = df["code"].str.startswith(("4", "8", "688"))
        df = df[~mask].reset_index(drop=True)
        cache_path.parent.mkdir(exist_ok=True, parents=True)
        df.to_csv(cache_path, index=False, encoding="utf-8")
        return df
    except ImportError:
        return pd.DataFrame(columns=["code", "name"])


# ============================================================
# 大盘(创业板指)K 线 + 状态判定
# ============================================================
def _ensure_benchmark_index_fresh():
    """P5A (2026-07-24): 增量从腾讯源拉最新 399006 K 线, append 到 index_399006.json

    历史根因: fetch_benchmark_kline 旧逻辑中本地 JSON 文件存在时从不走网络,
    且没有任何 cron/auto_refresh 脚本增量更新该 JSON — 导致大盘 K 线
    永久停留在最后 init 时的日期 (2026-07-17) 整周.

    本函数仅增量 (append), 绝不重写既有历史数据:
      - 本地 JSON 最后日期 = today / future: 不动, return
      - 否则从腾讯源拉 (n=250, ~1年) 足够覆盖任意 gap;
        覆盖本地对应日期, 写入临时文件 + 原子 rename.

    失败容错: 任何异常吞掉 (仅 stderr 一行 warn), 不阻塞 fetcher 原 fallback.
    """
    import sys as _sys
    if not INDEX_399006_PATH.exists():
        return
    try:
        with INDEX_399006_PATH.open() as f:
            raw = json.load(f)
        if not raw:
            return
        local_last = max(raw.keys())
        from datetime import date as _date
        today_str = _date.today().isoformat()
        if local_last >= today_str:
            return
        df_new = _tencent_kline_one("399006", n=250)
        if df_new is None or len(df_new) == 0:
            return
        df_new["date"] = df_new["date"].astype(str)
        df_new_today = df_new[df_new["date"] >= local_last].drop_duplicates(subset="date", keep="last")
        for _, row in df_new_today.iterrows():
            raw[row["date"]] = {
                "open": float(row.get("open", 0) or 0),
                "close": float(row.get("close", 0) or 0),
                "high": float(row.get("high", 0) or 0),
                "low": float(row.get("low", 0) or 0),
                "volume": float(row.get("volume", 0) or 0),
            }
        tmp = INDEX_399006_PATH.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump(raw, f, ensure_ascii=False, sort_keys=True)
        tmp.replace(INDEX_399006_PATH)
        print(f"[fetch_benchmark] JSON 增量 update: {local_last} → {today_str} (+{len(df_new_today)} bars)", file=_sys.stderr)
    except Exception as e:
        print(f"[fetch_benchmark] 增量 update 失败, 走原 fallback: {e}", file=_sys.stderr)


def fetch_benchmark_kline(n: int = 800) -> pd.DataFrame | None:
    """拉创业板指 399006 K 线

    优先从本地 index_399006.json 读 (2010 起),退化到腾讯源。
    返回 DataFrame: date, open, close, high, low, volume

    P5A (2026-07-24): 调用前先 _ensure_benchmark_index_fresh() 自动增量 update
    本地 JSON — 根除 "本地 JSON 卡在 init 日期" 的 silent failure.
    """
    _ensure_benchmark_index_fresh()

    if INDEX_399006_PATH.exists():
        try:
            with INDEX_399006_PATH.open() as f:
                raw = json.load(f)
            dates = sorted(raw.keys())
            if n > 0:
                dates = dates[-n:]
            rows = []
            for d in dates:
                v = raw[d]
                rows.append({
                    "date": d,
                    "open": float(v.get("open", 0) or 0),
                    "close": float(v.get("close", 0) or 0),
                    "high": float(v.get("high", 0) or 0),
                    "low": float(v.get("low", 0) or 0),
                    "volume": float(v.get("volume", 0) or 0),
                })
            df = pd.DataFrame(rows)
            if len(df) >= 250:
                return df
        except Exception:
            pass

    # 退化: 腾讯源
    return _tencent_kline_one(BENCHMARK_CODE, n)


@dataclass
class MarketState:
    """大盘状态机"""
    state: str = "横盘"           # 当前状态
    state_since: str = ""        # 状态起始日 YYYY-MM-DD
    ma20: float = 0.0
    ma20_slope: float = 0.0      # 百分比
    phase_bottom_remaining: int = 0  # 阶段底剩余天数


def _ma(series: np.ndarray, n: int) -> float:
    if len(series) < n:
        return float("nan")
    return float(series[-n:].mean())


def _slope_pct(series: np.ndarray, n: int) -> float:
    """20 日 MA 斜率(过去 n 天的累计百分比变化)"""
    if len(series) < n:
        return 0.0
    chunk = series[-n:]
    base = chunk[0]
    if base == 0:
        return 0.0
    return (chunk[-1] - base) / base * 100


def calc_market_state(benchmark_kline: pd.DataFrame) -> list[MarketState]:
    """每日生成大盘状态序列

    规则(说明书 §三):
      强势: 创业板收盘 > MA20 + MA20 斜率 > +0.5%
      弱势: 创业板收盘 < MA20 + MA20 斜率 < -0.5%
      横盘: 其他
      阶段底: 弱势/横盘 → 强势拐点 + 未来 7 天
    状态最小持续 5 天(避免频繁切换)
    """
    if benchmark_kline is None or len(benchmark_kline) < 60:
        return []

    closes = benchmark_kline["close"].astype(float).values
    dates = benchmark_kline["date"].astype(str).values

    # 每日 MA20 和斜率(过去 5 天变化)
    daily_ma20 = pd.Series(closes).rolling(20, min_periods=1).mean().values
    daily_slope = np.array([
        _slope_pct(daily_ma20[max(0, i - 5): i + 1], len(daily_ma20[max(0, i - 5): i + 1]))
        for i in range(len(daily_ma20))
    ])

    # 原始信号
    raw = []
    for i in range(len(closes)):
        if np.isnan(daily_ma20[i]):
            raw.append("横盘")
            continue
        if closes[i] > daily_ma20[i] and daily_slope[i] > 0.5:
            raw.append("强势")
        elif closes[i] < daily_ma20[i] and daily_slope[i] < -0.5:
            raw.append("弱势")
        else:
            raw.append("横盘")

    # 应用状态最小持续 5 天 + 阶段底窗口
    states: list[MarketState] = []
    cur_state = "横盘"
    cur_duration = 0
    phase_bottom_remaining = 0
    pending_strong = False  # 待转换的强势

    for i, sig in enumerate(raw):
        # 阶段底窗口: 弱势/横盘 → 强势时,延后 7 天保留为"阶段底"
        if phase_bottom_remaining > 0:
            states.append(MarketState(
                state="阶段底",
                state_since=dates[max(0, i - phase_bottom_remaining)],
                ma20=float(daily_ma20[i]),
                ma20_slope=float(daily_slope[i]),
                phase_bottom_remaining=phase_bottom_remaining,
            ))
            phase_bottom_remaining -= 1
            # 阶段底期内,保持"阶段底"状态(不切换)
            cur_state = "阶段底"
            cur_duration = 0
            continue

        # 状态持续期 < 5 → 保持原状态
        if cur_duration < MARKET_MIN_DURATION:
            if sig == cur_state:
                cur_duration += 1
            else:
                # 累计到 5 天才允许切换
                cur_duration += 1
            states.append(MarketState(
                state=cur_state,
                state_since=dates[max(0, i - cur_duration)],
                ma20=float(daily_ma20[i]),
                ma20_slope=float(daily_slope[i]),
            ))
            continue

        # 达到 5 天,允许切换
        if sig != cur_state:
            if sig == "强势" and cur_state in ("弱势", "横盘"):
                # 弱势/横盘 → 强势: 立即进入"阶段底"窗口
                cur_state = "阶段底"
                cur_duration = 0
                phase_bottom_remaining = PHASE_BOTTOM_WINDOW - 1
                states.append(MarketState(
                    state="阶段底",
                    state_since=dates[i],
                    ma20=float(daily_ma20[i]),
                    ma20_slope=float(daily_slope[i]),
                    phase_bottom_remaining=phase_bottom_remaining,
                ))
                continue
            else:
                cur_state = sig
                cur_duration = 1
        else:
            cur_duration += 1
        states.append(MarketState(
            state=cur_state,
            state_since=dates[max(0, i - cur_duration)],
            ma20=float(daily_ma20[i]),
            ma20_slope=float(daily_slope[i]),
        ))
    return states


# ============================================================
# 因子打分
# ============================================================
def _alpha_pct(series: np.ndarray, n: int) -> float:
    """n 日累计 α 百分比"""
    if len(series) < n + 1:
        return 0.0
    base = series[-(n + 1)]
    if base == 0:
        return 0.0
    return (series[-1] - base) / base * 100


def calc_score(stock_closes: np.ndarray, bench_closes: np.ndarray) -> float:
    """三因子打分: rank_20日α×40% + rank_5日α×40% + rank_10日α×20%

    注意: 这只是单只股票的原始 α,真实 score 需在截面上做 rank
    调用方 (run_backtest) 负责截面 rank。
    返回: (alpha_5, alpha_10, alpha_20) 三组原始值。
    """
    a5 = _alpha_pct(stock_closes, 5) - _alpha_pct(bench_closes, 5)
    a10 = _alpha_pct(stock_closes, 10) - _alpha_pct(bench_closes, 10)
    a20 = _alpha_pct(stock_closes, 20) - _alpha_pct(bench_closes, 20)
    return a5, a10, a20


def _is_early_bottom(stock_closes: np.ndarray, recent_window: int = 10,
                      long_window: int = 60) -> bool:
    """个股提前见底信号(说明书 §2.3)

    满足任一:
      - 10 日振幅 < 60 日振幅 × 0.5
      - 现价 > 60 日最低 × 1.05
      - alpha_10 改善(当前 > 5 天前)
    """
    if len(stock_closes) < long_window:
        return False
    cur_amp_10 = stock_closes[-recent_window:].max() - stock_closes[-recent_window:].min()
    cur_amp_60 = stock_closes[-long_window:].max() - stock_closes[-long_window:].min()
    if cur_amp_60 > 0 and cur_amp_10 < cur_amp_60 * 0.5:
        return True
    low_60 = stock_closes[-long_window:].min()
    if low_60 > 0 and stock_closes[-1] > low_60 * 1.05:
        return True
    if len(stock_closes) > recent_window + 5:
        alpha_now = stock_closes[-1] - stock_closes[-recent_window - 1]
        alpha_before = stock_closes[-recent_window - 1] - stock_closes[-(recent_window * 2) - 1]
        if alpha_now > alpha_before:
            return True
    return False


def _ma_value(series: np.ndarray, n: int) -> float:
    if len(series) < n:
        return float("nan")
    return float(series[-n:].mean())


# ============================================================
# 截面 rank 打分
# ============================================================
def cross_section_score(alphas_today: pd.DataFrame, is_bottom_phase: bool) -> pd.DataFrame:
    """对当日所有股票做截面百分位 rank,产出最终 score

    输入: alphas_today[code, alpha_5, alpha_10, alpha_20, close, ma200]
    输出: 增加列 score, candidate(是否候选,MA200 上 + score 达标)
    """
    df = alphas_today.copy()
    if df.empty:
        return df
    # 百分位 rank(0-100)
    df["r5"] = df["alpha_5"].rank(pct=True) * 100
    df["r10"] = df["alpha_10"].rank(pct=True) * 100
    df["r20"] = df["alpha_20"].rank(pct=True) * 100
    df["raw_score"] = df["r20"] * W_20D + df["r5"] * W_5D + df["r10"] * W_10D

    # MA200 过滤
    df["above_ma200"] = df["close"] > df["ma200"]
    df["score_valid"] = df["above_ma200"] & df["raw_score"].notna()
    # 见底加成
    df.loc[df["early_bottom"], "raw_score"] = df.loc[df["early_bottom"], "raw_score"] + EARLY_BOTTOM_BONUS
    # 截断到 100
    df["raw_score"] = df["raw_score"].clip(0, 100)
    # 入场门槛
    threshold = SCORE_THRESHOLD_BOTTOM if is_bottom_phase else SCORE_THRESHOLD
    df["candidate"] = df["score_valid"] & (df["raw_score"] >= threshold)
    df["score"] = df["raw_score"].round(1)
    return df


# ============================================================
# 调仓 / 持仓模拟
# ============================================================
@dataclass
class Position:
    code: str
    name: str
    entry_date: str
    entry_price: float
    highest_price: float
    shares: int = 0
    consecutive_loser_days: int = 0  # 连续跑输累计天数


@dataclass
class BacktestResult:
    """单次回测结果"""
    equity_curve: pd.DataFrame   # date, equity, position_size, market_state
    trades: pd.DataFrame         # date, code, name, action, price, reason
    holdings_log: pd.DataFrame   # date, code, name, weight
    final_stats: dict


def _daily_alpha(stock_closes: np.ndarray, bench_closes: np.ndarray, n: int) -> float:
    """n 日累计 α(stock - bench)"""
    if len(stock_closes) < n + 1 or len(bench_closes) < n + 1:
        return 0.0
    sa = (stock_closes[-1] - stock_closes[-n - 1]) / stock_closes[-n - 1]
    ba = (bench_closes[-1] - bench_closes[-n - 1]) / bench_closes[-n - 1]
    return (sa - ba) * 100


def run_backtest(
    klines: dict,                # code -> DataFrame(date, open, close, high, low)
    benchmark: pd.DataFrame,     # 创业板指 K 线
    name_map: dict,              # code -> name
    progress_cb: Callable | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> BacktestResult:
    """单次回测主循环 (2026-07-20 阶段 2 拆为 5 个子函数 + 调度器)

    Args:
        klines: 全市场 K 线字典(>200 天的)
        benchmark: 创业板指 K 线
        name_map: code -> 中文名
        progress_cb: 进度回调 fn(done, total, msg)
        start_date: 起始交易日(默认从 benchmark 上市 200 天后开始)
        end_date: 截止交易日(默认最新)
    """
    # === 0) 准备阶段 ===
    if benchmark is None or len(benchmark) < 250:
        raise ValueError("大盘数据不足")

    bench_dates = benchmark["date"].astype(str).values
    bench_closes = benchmark["close"].astype(float).values
    # 市场状态机 → 字符串序列 (POSITION_TABLE.get 需要 hashable 字符串 key)
    market_state_seq = [ms.state for ms in calc_market_state(benchmark)]

    if start_date is None:
        start_idx = 250  # 上市 200 天后开始, 留 50 天缓冲
    else:
        start_idx = int(np.searchsorted(bench_dates, start_date))
    if end_date is None:
        end_idx = len(bench_dates) - 1
    else:
        end_idx = int(np.searchsorted(bench_dates, end_date, side="right") - 1)
    total_days = end_idx - start_idx + 1
    if total_days <= 0:
        raise ValueError(f"回测区间太短: {total_days} 天")

    # 索引化所有股票 K 线
    valid_codes = []
    stock_closes_by_date = {}
    stock_date_arrays = {}
    stock_close_arrays = {}
    stock_high_by_date = {}
    for code, kl in klines.items():
        if kl is None or len(kl) < 200:
            continue
        d = kl["date"].astype(str).values
        c = kl["close"].astype(float).values
        h = kl["high"].astype(float).values
        idx = {d_i: i for i, d_i in enumerate(d)}
        stock_closes_by_date[code] = idx
        stock_date_arrays[code] = d
        stock_close_arrays[code] = c
        stock_high_by_date[code] = {d_i: h[i] for i, d_i in enumerate(d) if i < len(h)}
        valid_codes.append(code)

    if not valid_codes:
        raise ValueError("无有效股票 K 线")

    # 模拟状态
    portfolio_cash = 1.0
    positions: dict[str, Position] = {}
    equity_records = []
    trade_records = []
    holdings_log = []

    # === 主循环调度器 ===
    for day_i in range(start_idx, end_idx + 1):
        cur_date = bench_dates[day_i]
        bench_so_far = bench_closes[: day_i + 1]
        market_state = market_state_seq[day_i] if day_i < len(market_state_seq) else "横盘"
        position_pct = POSITION_TABLE.get(market_state, 0.6)

        # 1) 选候选 (α 计算 + 截面 rank)
        df_scored, has_rows = _step_select_candidates(
            cur_date, market_state, valid_codes,
            stock_closes_by_date, stock_date_arrays, stock_close_arrays, bench_so_far,
        )

        if not has_rows:
            equity_records.append({
                "date": cur_date, "equity": portfolio_cash, "n_holdings": 0,
                "position_pct": position_pct, "market_state": market_state,
                "realized_pnl": 0.0,
            })
            holdings_log.append({"date": cur_date, "holdings": ""})
            continue

        # 2) 检查退出 (止损 / 跑输)
        to_sell, stop_loss_triggered_today = _step_check_exit(
            positions, cur_date,
            stock_close_arrays, stock_closes_by_date, stock_high_by_date, bench_so_far,
        )

        # 3) 执行卖出
        _step_execute_sell(to_sell, positions, name_map, cur_date, trade_records)

        # 4) 入场
        _step_execute_buy(
            df_scored, positions, stop_loss_triggered_today,
            name_map, cur_date, trade_records,
        )

        # 5) 净值更新
        portfolio_cash = _step_update_equity(
            positions, cur_date, stock_date_arrays, stock_close_arrays, market_state,
            portfolio_cash, position_pct, equity_records, holdings_log,
        )

        if progress_cb and (day_i - start_idx) % 50 == 0:
            progress_cb(day_i - start_idx, total_days, f"回测 {cur_date}", None, "backtest")

    # === 6) 统计 ===
    return _step_finalize_stats(equity_records, trade_records, holdings_log, benchmark)


# ============================================================================
# 2026-07-20 阶段 2 拆 run_backtest: 5 个 _step_xxx 子函数
# 原则: 子函数不持有外部状态, 通过参数 + 返回值传递
# ============================================================================

def _step_select_candidates(
    cur_date, market_state, valid_codes,
    stock_closes_by_date, stock_date_arrays, stock_close_arrays, bench_so_far,
):
    """1) 计算所有股票 α + 截面 rank + 入场判断

    Returns:
        (df_scored, has_rows): df_scored 为截面评分 DataFrame, has_rows 标识是否有候选
    """
    rows = []
    for code in valid_codes:
        dates = stock_date_arrays[code]
        idx_map = stock_closes_by_date[code]
        pos = idx_map.get(cur_date)
        if pos is None or pos < 200:
            continue
        closes = stock_close_arrays[code][: pos + 1]
        a5, a10, a20 = calc_score(closes, bench_so_far)
        ma200 = _ma_value(closes, MA_FILTER)
        cur_close = float(closes[-1])
        early_bot = _is_early_bottom(closes)
        rows.append({
            "code": code,
            "alpha_5": a5,
            "alpha_10": a10,
            "alpha_20": a20,
            "close": cur_close,
            "ma200": ma200,
            "early_bottom": early_bot,
        })

    if not rows:
        return pd.DataFrame(), False

    df_today = pd.DataFrame(rows)
    df_scored = cross_section_score(df_today, is_bottom_phase=(market_state == "阶段底"))
    return df_scored, True


def _step_check_exit(
    positions, cur_date,
    stock_close_arrays, stock_closes_by_date, stock_high_by_date, bench_so_far,
):
    """2) 检查退出条件 (止损 / 跑输)

    标记当日止损触发 — 当日不补位(防反复交易)

    Returns:
        (to_sell, stop_loss_triggered_today)
    """
    to_sell = []
    for code, pos in positions.items():
        if code not in stock_close_arrays:
            continue
        idx_map = stock_closes_by_date[code]
        idx = idx_map.get(cur_date)
        if idx is None:
            continue
        cur_close = float(stock_close_arrays[code][idx])
        cur_high = stock_high_by_date[code].get(cur_date, cur_close)
        if cur_high > pos.highest_price:
            pos.highest_price = cur_high

        # 止损: 最高点回撤 10%
        if cur_close < pos.highest_price * (1 - STOP_LOSS_DRAWDOWN):
            to_sell.append((code, cur_close, "止损-10%回撤"))
            continue

        # 跑输 α 累计 10 天
        cur_alpha = _daily_alpha(
            stock_close_arrays[code][: idx + 1],
            bench_so_far,
            LOSER_DAYS,
        )
        if cur_alpha < 0:
            pos.consecutive_loser_days += 1
        else:
            pos.consecutive_loser_days = 0
        if pos.consecutive_loser_days >= LOSER_DAYS:
            to_sell.append((code, cur_close, f"连续{loser_days_str(LOSER_DAYS)}跑输α"))
            continue

    stop_loss_triggered_today = any(reason.startswith("止损") for _, _, reason in to_sell)
    return to_sell, stop_loss_triggered_today


def _step_execute_sell(to_sell, positions, name_map, cur_date, trade_records):
    """3) 执行卖出

    注: 净值跟踪改为"日终重估"模式(在 5) 中统一计算), 这里
    只更新 positions 和 trade_records, 不再调 cash。
    """
    for code, price, reason in to_sell:
        pos = positions.pop(code, None)
        if pos is None:
            continue
        sell_price = price * (1 - SLIPPAGE - STAMP_TAX_SELL - COMMISSION)
        pnl_ratio = (sell_price - pos.entry_price) / pos.entry_price
        trade_records.append({
            "date": cur_date, "code": code, "name": name_map.get(code, code),
            "action": "SELL", "price": round(price, 3), "reason": reason,
            "pnl_pct": round(pnl_ratio * 100, 2),
        })


def _step_execute_buy(df_scored, positions, stop_loss_triggered_today, name_map, cur_date, trade_records):
    """4) 入场: 从候选池补到 30 只
    """
    if stop_loss_triggered_today:
        return
    cur_holdings = set(positions.keys())
    n_need = POOL_TOP_N - len(cur_holdings)
    if n_need <= 0 or df_scored.empty:
        return
    candidates = (
        df_scored[df_scored["candidate"] & ~df_scored["code"].isin(cur_holdings)]
        .sort_values("score", ascending=False)
    )
    for _, row in candidates.head(n_need).iterrows():
        code = row["code"]
        if code in positions:
            continue
        price = float(row["close"])
        # 买入成本: 滑点 + 佣金
        buy_price = price * (1 + SLIPPAGE + COMMISSION)
        positions[code] = Position(
            code=code, name=name_map.get(code, code),
            entry_date=cur_date, entry_price=buy_price,
            highest_price=buy_price,
        )
        trade_records.append({
            "date": cur_date, "code": code, "name": name_map.get(code, code),
            "action": "BUY", "price": round(price, 3),
            "reason": f"score={row['score']:.1f}",
            "pnl_pct": 0.0,
        })


def _step_update_equity(
    positions, cur_date, stock_date_arrays, stock_close_arrays, market_state,
    portfolio_cash, position_pct, equity_records, holdings_log,
):
    """5) 净值: 等权 * 仓位比例

    用日线收益的几何乘 (避免浮点爆炸)

    Returns:
        portfolio_cash: 更新后的净值
    """
    import bisect
    if positions:
        day_rets = []
        for code in list(positions.keys()):
            dates_arr = stock_date_arrays[code]
            cur_idx = bisect.bisect_left(dates_arr, cur_date)
            if cur_idx >= len(dates_arr) or dates_arr[cur_idx] != cur_date:
                continue
            p = cur_idx - 1
            if p < 0:
                continue
            cur_close = float(stock_close_arrays[code][cur_idx])
            p_close = float(stock_close_arrays[code][p])
            if p_close <= 0:
                continue
            day_rets.append(cur_close / p_close - 1)
        if day_rets:
            # 零股填补到 30 只 (空仓位个股记为 0 收益)
            avg_ret = sum(day_rets) / POOL_TOP_N
            portfolio_cash *= (1 + avg_ret * position_pct)
    equity_records.append({
        "date": cur_date, "equity": portfolio_cash, "n_holdings": len(positions),
        "position_pct": position_pct, "market_state": market_state,
    })

    holdings_log.append({
        "date": cur_date,
        "holdings": ",".join(sorted(positions.keys())),
        "n_holdings": len(positions),
    })

    return portfolio_cash


def _step_finalize_stats(equity_records, trade_records, holdings_log, benchmark):
    """6) 统计: 回撤 + 跑赢基准 + 最终 stats dict + BacktestResult
    """
    eq_df = pd.DataFrame(equity_records)
    eq_df["date"] = pd.to_datetime(eq_df["date"])
    eq_df = eq_df.sort_values("date").reset_index(drop=True)

    n_days = len(eq_df)
    n_years = n_days / 250 if n_days > 0 else 0
    final_equity = float(eq_df["equity"].iloc[-1]) if n_days else 1.0
    cagr = (final_equity ** (1 / n_years) - 1) * 100 if n_years > 0 else 0
    # 最大回撤
    eq_df["peak"] = eq_df["equity"].cummax()
    eq_df["dd"] = (eq_df["equity"] - eq_df["peak"]) / eq_df["peak"]
    max_dd = float(eq_df["dd"].min() * 100) if not eq_df.empty else 0

    # 跑赢基准: 算创业板累计收益
    bench_subset = benchmark[
        (benchmark["date"] >= str(eq_df["date"].min().date()) if not eq_df.empty else False) &
        (benchmark["date"] <= str(eq_df["date"].max().date()) if not eq_df.empty else False)
    ]
    bench_ret = 0.0
    if len(bench_subset) > 1:
        b0 = float(bench_subset["close"].iloc[0])
        b1 = float(bench_subset["close"].iloc[-1])
        if b0 > 0:
            bench_ret = (b1 / b0 - 1) * 100

    stats = {
        "final_equity": final_equity,
        "cagr_pct": round(cagr, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "n_trades": len(trade_records),
        "n_buy": sum(1 for t in trade_records if t["action"] == "BUY"),
        "n_sell": sum(1 for t in trade_records if t["action"] == "SELL"),
        "n_days": n_days,
        "n_years": round(n_years, 1),
        "bench_cumret_pct": round(bench_ret, 2),
        "alpha_vs_bench_pct": round((final_equity - 1) * 100 - bench_ret, 2),
    }

    return BacktestResult(
        equity_curve=eq_df,
        trades=pd.DataFrame(trade_records),
        holdings_log=pd.DataFrame(holdings_log),
        final_stats=stats,
    )
def loser_days_str(n: int) -> str:
    return f"{n}日"


# ============================================================
# 全市场 K 线缓存
# ============================================================
def _parse_kline_json(path) -> pd.DataFrame | None:
    """2026-07-21 阶段 2 P1: 抽自 _load_local_klines, 解析单个 JSON 文件

    Returns: DataFrame(date, open, close, high, low, volume) 或 None (无效)
    """
    stem = path.stem
    if "_" not in stem:
        return None
    prefix, code = stem.split("_", 1)
    if len(code) != 6 or not code.isdigit():
        return None
    try:
        with path.open() as f:
            raw = json.load(f)
    except Exception:
        return None
    if not raw:
        return None
    dates = sorted(raw.keys())
    rows = []
    for d in dates:
        v = raw[d]
        close = v.get("close")
        if close is None or close == 0 or (isinstance(close, float) and math.isnan(close)):
            continue
        rows.append({
            "date": d,
            "open": float(v.get("open", 0) or 0),
            "close": float(close),
            "high": float(v.get("high", 0) or 0),
            "low": float(v.get("low", 0) or 0),
            "volume": float(v.get("volume", 0) or 0),
        })
    if len(rows) < 200:
        return None
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def _load_local_klines(codes: list | None = None,
                        progress_cb: Callable | None = None) -> dict:
    """从本地 stock_kline/ 目录加载 K 线 (2019-01 ~ 2026-07)

    组装器: 调 _parse_kline_json 处理每个文件 + 收集结果
    """
    if not STOCK_KLINE_DIR.exists():
        return {}

    out: dict[str, pd.DataFrame] = {}
    files = list(STOCK_KLINE_DIR.glob("*.json"))
    total = len(files)
    for i, p in enumerate(files):
        stem = p.stem
        if "_" not in stem:
            continue
        prefix, code = stem.split("_", 1)
        if len(code) != 6 or not code.isdigit():
            continue
        if codes is not None and code not in codes:
            continue
        df = _parse_kline_json(p)
        if df is None:
            continue
        out[code] = df

        if progress_cb and (i + 1) % 200 == 0:
            progress_cb(i + 1, total, f"加载本地 K 线 {i+1}/{total}", None, "kline")
    if progress_cb:
        progress_cb(total, total, f"本地 K 线加载完成: {len(out)} 只", None, "kline")
    return out


def fetch_market_klines(force: bool = False, n_days: int = 0,
                         max_workers: int = 20,
                         progress_cb: Callable | None = None) -> tuple[dict, pd.DataFrame]:
    """加载全市场 K 线 + 创业板指

    Args:
        force: True 忽略本地 pickle 缓存, 强制重读 JSON
        n_days: 0=全部,否则只取最后 n_days 天
    Returns: (klines_dict, benchmark_df)
    """
    cache_dir = INTERNAL_DATA_DIR
    cache_dir.mkdir(exist_ok=True, parents=True)
    cache_key = date.today().strftime("%Y%m%d")
    cache_path = cache_dir / f"market_{cache_key}.pkl"

    if not force and cache_path.exists():
        try:
            with cache_path.open("rb") as f:
                klines, benchmark = pickle.load(f)
            if progress_cb:
                progress_cb(100, 100, f"命中缓存 {len(klines)} 只", None, "done")
            return klines, benchmark
        except Exception:
            pass

    pool = get_a_stock_pool()
    if pool.empty:
        raise RuntimeError("无法获取股票池")
    codes = set(pool["code"].tolist())
    name_map = dict(zip(pool["code"], pool["name"]))

    if progress_cb:
        progress_cb(0, len(codes), f"加载本地全市场 K 线 ({len(codes)} 只) ...", None, "kline")

    # 本地加载(快速, 5-10s)
    klines = _load_local_klines(codes=codes, progress_cb=progress_cb)

    # 如果本地加载不够(可能部分股票未下载),补充拉腾讯源
    missing = codes - set(klines.keys())
    if missing:
        if progress_cb:
            progress_cb(len(klines), len(codes), f"补拉 {len(missing)} 只缺失 K 线 ...", None, "kline")
        extra = _tencent_batch_klines(list(missing), n=600, max_workers=max_workers)
        for c, kl in extra.items():
            if kl is not None and len(kl) >= 100:
                klines[c] = kl.reset_index(drop=True)

    benchmark = fetch_benchmark_kline(n=0)

    # 切片: 只保留最后 n_days
    if n_days and n_days > 0:
        for c in list(klines.keys()):
            klines[c] = klines[c].tail(n_days).reset_index(drop=True)
        if benchmark is not None:
            benchmark = benchmark.tail(n_days).reset_index(drop=True)

    # 落盘 pickle(加快下次启动)
    try:
        with cache_path.open("wb") as f:
            pickle.dump((klines, benchmark), f)
    except Exception:
        pass

    if progress_cb:
        progress_cb(100, 100, f"完成: {len(klines)} 只 K 线 + 创业板指", None, "done")

    return klines, benchmark


# ============================================================
# 下一交易日调仓清单
# ============================================================
def _resolve_asof_date(klines, benchmark, asof_date):
    """解析 asof_date: p90 算法 + benchmark 兜底"""
    bench_dates = benchmark["date"].astype(str).values
    bench_closes = benchmark["close"].astype(float).values

    if asof_date is None:
        # [FIX] Bug #3 - asof date was 21 days stale due to min() bug
        from datetime import datetime as _dt
        stock_last_dates = []
        for code, kl in klines.items():
            if kl is None or len(kl) < 200:
                continue
            d = str(kl["date"].iloc[-1])
            try:
                stock_last_dates.append(_dt.strptime(d, "%Y-%m-%d"))
            except ValueError:
                continue
        bench_last = _dt.strptime(str(bench_dates[-1]), "%Y-%m-%d")
        if stock_last_dates:
            stock_last_dates.sort()
            idx = int(len(stock_last_dates) * 0.9)
            idx = min(idx, len(stock_last_dates) - 1)
            p90 = stock_last_dates[idx]
            asof_date = min(p90, bench_last).strftime("%Y-%m-%d")
        else:
            asof_date = str(bench_dates[-1])

    # 找 asof_date 对应的大盘索引
    asof_idx = None
    for i, d in enumerate(bench_dates):
        if d == asof_date:
            asof_idx = i
            break
    if asof_idx is None:
        for i in range(len(bench_dates) - 1, -1, -1):
            if bench_dates[i] <= asof_date:
                asof_idx = i
                break
    if asof_idx is None or asof_idx < 250:
        return None, None, None, f"找不到 asof_date={asof_date} (仅 399006 有 {len(bench_dates)} 天)"

    bench_so_far = bench_closes[: asof_idx + 1]
    return asof_date, asof_idx, bench_so_far, None


def _compute_cross_section_scores(klines, name_map, asof_date, bench_so_far, market_state):
    """算当日所有股票 α + 截面打分"""
    rows = []
    for code, kl in klines.items():
        if kl is None or len(kl) < 200:
            continue
        d = kl["date"].astype(str).values
        if asof_date not in d:
            continue
        pos = list(d).index(asof_date)
        if pos < 200:
            continue
        closes = kl["close"].astype(float).values[: pos + 1]
        highs = kl["high"].astype(float).values[: pos + 1]
        a5, a10, a20 = calc_score(closes, bench_so_far)
        ma200 = _ma_value(closes, MA_FILTER)
        cur_close = float(closes[-1])
        cur_high = float(highs[-1])
        early_bot = _is_early_bottom(closes)
        n_listing = pos + 1
        rows.append({
            "code": code, "name": name_map.get(code, code),
            "close": cur_close, "ma200": ma200,
            "alpha_5": a5, "alpha_10": a10, "alpha_20": a20,
            "early_bottom": early_bot,
            "n_listing_days": n_listing,
        })

    if not rows:
        return None

    df = pd.DataFrame(rows)
    df_scored = cross_section_score(df, is_bottom_phase=(market_state == "阶段底"))
    return df_scored


def _select_top_picks(df_scored, asof_date):
    """筛选 Top 30 候选"""
    df_candidate = df_scored[df_scored["candidate"]].sort_values("score", ascending=False)
    top_picks = df_candidate.head(POOL_TOP_N)
    return top_picks


def _build_order_lists(top_picks, df_candidate, market_state):
    """构建调仓清单: hold_list / sell_candidates / add_candidates"""
    hold_list = []
    for _, row in top_picks.iterrows():
        hold_list.append({
            "code": row["code"], "name": row["name"],
            "price": round(float(row["close"]), 3),
            "score": round(float(row["score"]), 1),
            "alpha_5": round(float(row["alpha_5"]), 2),
            "alpha_10": round(float(row["alpha_10"]), 2),
            "alpha_20": round(float(row["alpha_20"]), 2),
        })

    # 调出候选
    bottom5 = top_picks.tail(5)
    sell_candidates = []
    threshold = SCORE_THRESHOLD_BOTTOM if market_state == "阶段底" else SCORE_THRESHOLD
    for _, row in bottom5.iterrows():
        if row["score"] < threshold + 5:
            sell_candidates.append({
                "code": row["code"], "name": row["name"],
                "price": round(float(row["close"]), 3),
                "score": round(float(row["score"]), 1),
                "reason": f"分数{row['score']:.1f}接近门槛{threshold}",
            })

    # 调入候选
    top_codes = set(top_picks["code"].tolist())
    bottom_score = float(top_picks.iloc[-1]["score"]) if len(top_picks) else 0
    add_candidates = (
        df_candidate[~df_candidate["code"].isin(top_codes)]
        .loc[lambda d: d["score"] > bottom_score]
        .head(10)
    )
    add_list = []
    for _, row in add_candidates.iterrows():
        add_list.append({
            "code": row["code"], "name": row["name"],
            "price": round(float(row["close"]), 3),
            "score": round(float(row["score"]), 1),
            "alpha_5": round(float(row["alpha_5"]), 2),
            "reason": f"score={row['score']:.1f} > Top 30 末位 {bottom_score:.1f}",
        })

    return hold_list, sell_candidates, add_list


def generate_daily_orders(
    klines: dict, benchmark: pd.DataFrame, name_map: dict,
    asof_date: str | None = None,
) -> dict:
    """生成"今日应执行的调仓清单"(对应说明书 §7.1)

    调度器 (2026-07-22 P2 拆解): 100L → 4 子函数
      1. _resolve_asof_date: p90 算法 + benchmark 兜底
      2. _compute_cross_section_scores: 当日 α + 截面打分
      3. _select_top_picks: Top 30 候选筛选
      4. _build_order_lists: hold_list + sell + add

    流程:
      1. 取 asof_date 当日所有股票 α, 算截面 score
      2. 检查当前虚拟持仓, 看是否有触发退出条件的
      3. 从候选 Top 30 中选未持仓的, 补到 30 只
      4. 输出: 卖出清单 + 买入清单
    """
    if benchmark is None or len(benchmark) < 250:
        return {"ok": False, "error": "无大盘数据"}

    # 1. asof_date 解析
    asof_date, asof_idx, bench_so_far, err = _resolve_asof_date(klines, benchmark, asof_date)
    if err:
        return {"ok": False, "error": err}

    # 2. 大盘状态
    market_states = calc_market_state(benchmark)
    market_state = market_states[asof_idx].state if asof_idx < len(market_states) else "横盘"
    position_pct = POSITION_TABLE.get(market_state, 0.6)
    ma20 = market_states[asof_idx].ma20 if asof_idx < len(market_states) else 0
    ma20_slope = market_states[asof_idx].ma20_slope if asof_idx < len(market_states) else 0

    # 3. 截面打分
    df_scored = _compute_cross_section_scores(klines, name_map, asof_date, bench_so_far, market_state)
    if df_scored is None:
        return {"ok": False, "error": "无当日股票数据"}

    # 4. Top 30 候选
    top_picks = _select_top_picks(df_scored, asof_date)

    # 5. 构建调仓清单
    hold_list, sell_candidates, add_list = _build_order_lists(top_picks, df_scored[df_scored["candidate"]], market_state)

    # 6. 调仓总结
    n_buy = len(add_list)
    n_sell = len(sell_candidates)
    summary_msg = "无需调仓"
    if n_buy > 0 or n_sell > 0:
        summary_msg = f"调出 {n_sell} 只, 调入 {n_buy} 只候选 (实操时选最高分补入)"

    return {
        "ok": True,
        "asof_date": asof_date,
        "market_state": market_state,
        "ma20": round(ma20, 2),
        "ma20_slope_pct": round(ma20_slope, 2),
        "position_pct": position_pct,
        "n_total": len(df_scored),
        "n_candidates": int(df_scored["candidate"].sum()),
        "summary": summary_msg,
        "hold_list": hold_list,
        "sell_candidates": sell_candidates,
        "add_candidates": add_list,
        "top_picks": top_picks[["code", "name", "close", "score", "alpha_5", "alpha_10", "alpha_20"]]
            .to_dict("records"),
    }