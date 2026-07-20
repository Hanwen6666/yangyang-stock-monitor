"""
强弱选股策略 Tab — 基于策略说明书 v3.0 实现

核心逻辑（来自 /home/ubuntu/.openclaw/workspace/strategy_manual.md v3.0）:
  1. 因子打分: rank(20日α) * 40% + rank(5日α) * 40% + rank(10日α) * 20%
  2. MA200 过滤: 收盘价 > 200日均线 (硬过滤, 剔除弱势股)
  3. 大盘状态: 创业板指 (399006) vs MA20 + 斜率 → 强势/阶段底/横盘/弱势
  4. 见底加成: 弱势/横盘期, 个股提前见底 +15 分 (三条件任一)
  5. 候选池: Top 30 分数 ≥ 60 (阶段底期 50)
  6. 调仓信号: 单日版 (今日 Top 30 + 模拟 30 日前持仓 + -10% 止损)

性能要点:
  - 4961 只全市场 CSV 缓存, 用 st.cache_resource 加载一次
  - 因子计算 (5/10/20日 α、MA200) 缓存在 DataFrame 里
  - 截面排名用 pandas method='average'
  - 默认只跑最近 250 个交易日 (≈ 1.2 年) 够用

输出:
  - 4 个 KPI 卡: 大盘状态 / 建议仓位 / 候选数 / 调仓动作
  - 候选池 Top 30 表格: 分数 / 三个 α / 站上 MA200 / 见底信号
  - 调仓信号表: 退出 / 新进 / 当前持仓
  - 大盘状态历史条 (最近 60 日)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

# 复用项目内 lib
_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent.parent))
from lib.constants import (  # noqa: E402
    BG_PANEL, BG_PANEL_HI, BORDER, BORDER_HI,
    TEXT, TEXT_MUTED, TEXT_DIM, ACCENT_UP, ACCENT_DN,
)
from lib.ui_components import kpi_card  # noqa: E402


# ============================================================
# 配置 / 常量
# ============================================================
STOCK_CACHE_DIR = Path("/home/ubuntu/.openclaw/workspace/stock_cache")
DATA_CACHE_DIR = Path("/home/ubuntu/.openclaw/workspace/data_cache")
BENCHMARK_CODE = "399006"  # 创业板指
BENCHMARK_NAME = "创业板指"
# benchmark 优先从 data_cache 取 (列全 + 日期长 2010 至今)
BENCHMARK_CANDIDATES = [
    DATA_CACHE_DIR / f"创业板指_{BENCHMARK_CODE}.csv",
    DATA_CACHE_DIR / f"{BENCHMARK_CODE}.csv",
    STOCK_CACHE_DIR / f"{BENCHMARK_CODE}.csv",
]

# v3.0 策略参数（与说明书一致）
TOP_N = 30
SCORE_THRESHOLD_NORMAL = 60.0
SCORE_THRESHOLD_BOTTOM = 50.0
STOP_DRAWDOWN = 0.10  # 最高点回撤 10%
SEEK_BOTTOM_BONUS = 15.0
MA200_PERIOD = 200
ALPHA_WINDOWS = (5, 10, 20)
# 三个 α 排名加权, 总和 0.85 (留 0.15 空间给见底加成 +15, 避免全员 +15 后被 clip 到 100)
ALPHA_WEIGHTS = (0.35, 0.15, 0.35)  # 5日、10日、20日
BOTTOM_WINDOW = 7  # 阶段底窗口天数
MIN_LIST_DAYS = MA200_PERIOD  # 至少 200 个交易日才能算 MA200
REBALANCE_INTERVAL_DAYS = 5  # 调仓间隔 (每周一次, 跟说明书"平均 1-2 个月调仓"粗估)
                                  # 实际是只重选 Top 30, 止损仍每日检查

# 仓位方案 C（与 7-14 用户决策一致）
POSITION_RATIO = {
    "强势": 1.00,
    "阶段底": 1.00,
    "横盘": 0.60,
    "弱势": 0.40,
}

# 大盘状态颜色
REGIME_COLORS = {
    "强势": "#26d97f",
    "阶段底": "#ffd54f",
    "横盘": "#7a7f96",
    "弱势": "#ff5577",
}

# 排除板块前缀: 北交所 8/4 开头, 科创板 688 开头
EXCLUDE_PREFIXES = ("688", "8", "4")


# ============================================================
# 数据加载
# ============================================================
@st.cache_resource(show_spinner="加载全市场 K 线...")
def _load_all_stocks():
    """加载全市场 stock_cache (4961 只), 返回 {
        panel: 长表 (date, code, name, close, volume),
        pool:   {code: DataFrame}  (原表, 兼向下文),
        meta:   code/name 元信息
    }

    为性能考虑, 加载后转长表一次, 因子计算全程用 groupby 向量化
    """
    if not STOCK_CACHE_DIR.exists():
        return {"panel": pd.DataFrame(), "pool": {}, "meta": pd.DataFrame()}
    pool = {}
    meta_rows = []
    for csv_path in STOCK_CACHE_DIR.glob("*.csv"):
        code = csv_path.stem
        # 过滤北交所 + 科创板
        if code.startswith(EXCLUDE_PREFIXES):
            continue
        try:
            df = pd.read_csv(csv_path, dtype={"code": str})
        except Exception:
            continue
        if df.empty:
            continue
        # 统一日期
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        if len(df) < 60:  # 至少 60 个交易日
            continue
        name = str(df["name"].iloc[-1]) if "name" in df.columns else code
        pool[code] = df
        meta_rows.append({
            "code": code,
            "name": name,
            "n_points": len(df),
            "first_date": df["date"].iloc[0],
            "last_date": df["date"].iloc[-1],
            "last_close": float(df["close"].iloc[-1]) if "close" in df.columns else 0.0,
        })
    meta = pd.DataFrame(meta_rows)
    if not meta.empty:
        meta["code"] = meta["code"].astype(str)
        meta = meta.sort_values("code").reset_index(drop=True)
    # 拼长表 (仅 date, code, name, close, volume) -- groupby 因子用
    parts = []
    for code, df in pool.items():
        d = df[["date", "close", "volume", "code", "name"]].copy()
        d["code"] = d["code"].astype(str).str.zfill(6)
        parts.append(d)
    if parts:
        panel = pd.concat(parts, ignore_index=True)
        panel = panel.sort_values(["code", "date"]).reset_index(drop=True)
    else:
        panel = pd.DataFrame()
    return {"panel": panel, "pool": pool, "meta": meta}


@st.cache_resource(show_spinner=False)
def _load_benchmark():
    """加载创业板指 benchmark (399006)

    优先级: data_cache/创业板指_399006.csv > data_cache/399006.csv > stock_cache/399006.csv
    列名兼容: date / DateTime
    """
    for p in BENCHMARK_CANDIDATES:
        if p.exists():
            try:
                df = pd.read_csv(p)
                # 列名兼容
                if "DateTime" in df.columns and "date" not in df.columns:
                    df = df.rename(columns={"DateTime": "date"})
                if "date" in df.columns:
                    df["date"] = pd.to_datetime(df["date"], errors="coerce")
                    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
                if not df.empty and "close" in df.columns and len(df) >= 60:
                    return df
            except Exception:
                continue
    return pd.DataFrame()


# ============================================================
# 因子计算
# ============================================================
def _alpha(close: pd.Series, n: int) -> float:
    """n 日累计涨幅 (收益率)"""
    if len(close) < n + 1 or close.iloc[-(n + 1)] <= 0:
        return 0.0
    return float(close.iloc[-1] / close.iloc[-(n + 1)] - 1.0)


def _ma(close: pd.Series, n: int) -> float:
    if len(close) < n:
        return float("nan")
    return float(close.iloc[-n:].mean())


def _ma_slope(close: pd.Series, n: int = 20) -> float:
    """MA20 斜率 (20 日均线相对 5 日前的变化率)"""
    if len(close) < n + 5:
        return 0.0
    ma_now = close.iloc[-n:].mean()
    ma_prev = close.iloc[-(n + 5):-5].mean()
    if ma_prev <= 0:
        return 0.0
    return float(ma_now / ma_prev - 1.0)


def _is_seek_bottom(df: pd.DataFrame) -> bool:
    """个股提前见底信号 (说明书 2.3)"""
    close = df["close"]
    if len(close) < 60:
        return False
    # 条件1: 10日振幅 < 60日振幅 × 0.5
    high10 = df["high"].iloc[-10:].max() if "high" in df.columns else close.iloc[-10:].max()
    low10 = df["low"].iloc[-10:].min() if "low" in df.columns else close.iloc[-10:].min()
    amp10 = (high10 - low10) / max(low10, 0.01)
    high60 = df["high"].iloc[-60:].max() if "high" in df.columns else close.iloc[-60:].max()
    low60 = df["low"].iloc[-60:].min() if "low" in df.columns else close.iloc[-60:].min()
    amp60 = (high60 - low60) / max(low60, 0.01)
    cond1 = amp10 < amp60 * 0.5
    # 条件2: 现价 > 60日最低 × 1.05
    cond2 = close.iloc[-1] > low60 * 1.05
    # 条件3: alpha_10 改善 (当前 > 5天前)
    if len(close) < 16:
        cond3 = False
    else:
        a10_now = close.iloc[-1] / close.iloc[-11] - 1
        a10_prev = close.iloc[-6] / close.iloc[-16] - 1
        cond3 = a10_now > a10_prev
    return cond1 or cond2 or cond3


# ============================================================
# 大盘状态
# ============================================================
def compute_market_regime(bench: pd.DataFrame) -> pd.DataFrame:
    """对创业板指每日判定状态: 强势 / 阶段底 / 横盘 / 弱势

    状态最小持续 5 天, 避免频繁切换
    """
    if bench.empty or "close" not in bench.columns:
        return pd.DataFrame()
    close = bench["close"].reset_index(drop=True)
    n = len(close)
    raw_state = []
    for i in range(20, n):  # 至少 20 天
        win = close.iloc[: i + 1]
        c = win.iloc[-1]
        ma20 = win.iloc[-20:].mean()
        slope = _ma_slope(win, 20)
        if c > ma20 and slope > 0.005:
            raw_state.append("强势")
        elif c < ma20 and slope < -0.005:
            raw_state.append("弱势")
        else:
            raw_state.append("横盘")
    # 阶段底: 弱势/横盘 → 强势拐点 + 未来 BOTTOM_WINDOW 天
    final = raw_state[:]
    for i in range(len(final)):
        if final[i] == "强势":
            # 看前面 5 天内是否有过弱势或横盘
            prev = final[max(0, i - 5):i]
            if any(s in ("弱势", "横盘") for s in prev):
                # 拐点, 标记未来 7 天为阶段底
                for j in range(i, min(i + BOTTOM_WINDOW, len(final))):
                    if final[j] == "强势":
                        final[j] = "阶段底"
                    else:
                        break
    # 平滑: 5 天最小持续 (出现 < 5 天的状态回退到前一个状态)
    # smoothed 是 final 的拷贝, 然后根据窗口判断是否回退
    smoothed = final[:]
    for i in range(len(final)):
        if i < 5 or i >= len(final) - 5:
            continue
        window_before = final[max(0, i - 5):i]
        window_after = final[i + 1: i + 6]
        cur = final[i]
        # 当前状态在前后 5 天窗口中出现 < 2 次 → 设为窗口中除自己外出现最多的状态
        cnt_before = window_before.count(cur)
        cnt_after = window_after.count(cur)
        if cnt_before + cnt_after < 2:  # 孤立的当前状态
            # 取窗口中除当前状态外最多的
            full_window = window_before + window_after
            others = [s for s in full_window if s != cur]
            if others:
                smoothed[i] = max(set(others), key=others.count)
    dates = bench["date"].iloc[20:].reset_index(drop=True)
    return pd.DataFrame({"date": dates, "regime": smoothed, "raw": raw_state})


# ============================================================
# 策略核心: 每日打分 + Top 候选
# ============================================================
@st.cache_data(ttl=1800, show_spinner="计算因子 + 截面排名...")
def _compute_top_candidates_uncached(asof_date_str: str) -> pd.DataFrame:
    """截至 asof_date 的 Top 候选池 (向量化版)

    步骤:
      1. 加载长表 + benchmark
      2. groupby(code).tail(MA200_PERIOD+1) → 算 MA200、5/10/20日α
      3. 截面排名 → 分数 = rank(20α)*0.4 + rank(5α)*0.4 + rank(10α)*0.2
      4. MA200 过滤 + 分数门槛
      5. 弱势/横盘期: 见底加成 +15 (同样向量化)
    """
    data = _load_all_stocks()
    panel = data["panel"]
    pool = data["pool"]
    if panel.empty:
        return pd.DataFrame()
    bench = _load_benchmark()
    if bench.empty:
        return pd.DataFrame()

    asof_date = pd.Timestamp(asof_date_str)
    # 截断 benchmark 到 asof_date
    bench_cut = bench[bench["date"] <= asof_date].copy()
    if len(bench_cut) < 60:
        return pd.DataFrame()
    b_alpha5 = _alpha(bench_cut["close"], 5)
    b_alpha10 = _alpha(bench_cut["close"], 10)
    b_alpha20 = _alpha(bench_cut["close"], 20)
    b_ma20 = _ma(bench_cut["close"], 20)
    b_slope = _ma_slope(bench_cut["close"], 20)
    b_close = float(bench_cut["close"].iloc[-1])

    # 大盘状态
    if b_close > b_ma20 and b_slope > 0.005:
        regime = "强势"
    elif b_close < b_ma20 and b_slope < -0.005:
        regime = "弱势"
    else:
        regime = "横盘"
    if regime == "强势":
        win_prev = bench_cut["close"].iloc[-6:-1]
        prev_slope = 0.0
        if len(win_prev) >= 20:
            ma20_prev = win_prev.iloc[-20:].mean()
            prev_slope = (win_prev.iloc[-1] / ma20_prev - 1) if ma20_prev > 0 else 0
        if prev_slope < 0.001:
            regime = "阶段底"

    # 截断到 asof_date
    panel_cut = panel[panel["date"] <= asof_date].copy()
    # 每只代码取最后 220 行 (足够算 5/10/20/60/200 指标)
    last_n = MA200_PERIOD + 20
    panel_recent = panel_cut.groupby("code").tail(last_n)

    def _safe_pct(s, k):
        # s 是该股的 close 序列, k 是回看天数
        if len(s) < k + 1:
            return 0.0
        a = float(s.iloc[-1] / s.iloc[-(k + 1)] - 1)
        return a

    # 算 5/10/20 日α (收益率), 60 日最低, MA200, 现价, 见底信号 三个原始值
    def _per_code(s: pd.DataFrame) -> pd.Series:
        s = s.sort_values("date")
        c = s["close"]
        n = len(c)
        if n < MIN_LIST_DAYS:
            return pd.Series({
                "close": np.nan, "ma200": np.nan,
                "alpha_5": np.nan, "alpha_10": np.nan, "alpha_20": np.nan,
                "low60": np.nan, "high60": np.nan,
                "low10": np.nan, "high10": np.nan,
                "alpha_10_now": np.nan, "alpha_10_prev": np.nan,
                "n_points": n,
            })
        ma200 = float(c.iloc[-MA200_PERIOD:].mean()) if n >= MA200_PERIOD else float("nan")
        if np.isnan(ma200) or c.iloc[-1] <= ma200:
            ma200 = float("nan")  # 标记为不参与
        a5 = _safe_pct(c, 5)
        a10 = _safe_pct(c, 10)
        a20 = _safe_pct(c, 20)
        low60 = float(c.iloc[-60:].min()) if n >= 60 else float("nan")
        high60 = float(c.iloc[-60:].max()) if n >= 60 else float("nan")
        # 见底信号 三个原始值 (说明书 2.3)
        low10 = float(c.iloc[-10:].min()) if n >= 10 else float("nan")
        high10 = float(c.iloc[-10:].max()) if n >= 10 else float("nan")
        if n >= 16:
            alpha_10_now = float(c.iloc[-1] / c.iloc[-11] - 1)
            alpha_10_prev = float(c.iloc[-6] / c.iloc[-16] - 1)
        else:
            alpha_10_now = float("nan")
            alpha_10_prev = float("nan")
        return pd.Series({
            "close": float(c.iloc[-1]),
            "ma200": ma200,
            "alpha_5": a5 - b_alpha5,
            "alpha_10": a10 - b_alpha10,
            "alpha_20": a20 - b_alpha20,
            "low60": low60,
            "high60": high60,
            "low10": low10,
            "high10": high10,
            "alpha_10_now": alpha_10_now,
            "alpha_10_prev": alpha_10_prev,
            "n_points": n,
        })

    # groupby + apply -- groupby.apply 返回 DataFrame (行=code, 列=返回 Series 的字段)
    feats = panel_recent.groupby("code").apply(_per_code, include_groups=False)
    if feats.empty or not isinstance(feats, pd.DataFrame):
        return pd.DataFrame()
    feats = feats.reset_index()
    # 过滤 ma200 为 NaN 的行 (未上市 200 天 或 未站上 MA200)
    if "ma200" not in feats.columns:
        return pd.DataFrame()
    feats = feats[feats["ma200"].notna()]
    if feats.empty:
        return pd.DataFrame()
    # 现价必须在 MA200 上方 (现已在 _per_code 里过滤)
    feats["above_ma200_pct"] = feats["close"] / feats["ma200"] - 1

    # 排名 (0-100)
    for w_name in ("alpha_5", "alpha_10", "alpha_20"):
        feats[f"rank_{w_name}"] = feats[w_name].rank(method="average", pct=True) * 100
    # 基础分 = rank_加权(0-100), 见底加成最多 +15
    # 排名权重各 0.4/0.2/0.4, rank_x 都在 0-100, 加权后 0-100
    feats["score"] = (
        feats["rank_alpha_5"] * ALPHA_WEIGHTS[0]
        + feats["rank_alpha_10"] * ALPHA_WEIGHTS[1]
        + feats["rank_alpha_20"] * ALPHA_WEIGHTS[2]
    )

    # 见底加成 (向量化: 三个条件都参与)
    # 说明书 2.3: 满足任一即触发 +15
    #   条件 1: 10 日振幅 < 60 日振幅 × 0.5
    #   条件 2: 现价 > 60 日最低 × 1.05
    #   条件 3: alpha_10 改善 (当前 > 5 天前)
    # 见底加成 (向量化: 三个条件都参与)
    # 说明书 2.3: 弱势/横盘期, 个股提前止跌 +15
    #   条件 1: 10 日振幅 < 60 日振幅 × 0.5 (近期低波动, 走出底部)
    #   条件 2: 现价 > 60 日最低 × 1.05 (已脱离低点)
    #   条件 3: alpha_10 改善 (当前 > 5 天前, 动量向上)
    # 设计决定: 只在 弱势 期加成 (横盘是中性, 不需要找底部)
    # 防滥用: 弱势期也要求至少满足 2 个条件 (条件 2 几乎人人满足, 单一条件信号弱)
    if regime == "弱势":
        amp10 = (feats["high10"] - feats["low10"]) / feats["low10"].clip(lower=0.01)
        amp60 = (feats["high60"] - feats["low60"]) / feats["low60"].clip(lower=0.01)
        cond1 = amp10 < amp60 * 0.5
        cond2 = feats["close"] > feats["low60"] * 1.05
        cond3 = feats["alpha_10_now"] > feats["alpha_10_prev"]
        n_cond = cond1.astype(int) + cond2.astype(int) + cond3.fillna(False).astype(int)
        feats["seek_bottom"] = (n_cond >= 2).fillna(False)
        feats.loc[feats["seek_bottom"], "score"] += SEEK_BOTTOM_BONUS
    else:
        feats["seek_bottom"] = False
    # 封顶 100 (弱势期加成后最多 100; 实际上加 +15 后只有弱势期才可能超 85)
    feats["score"] = feats["score"].clip(upper=100.0)

    # 合并 name
    name_map = data["meta"].set_index("code")["name"].to_dict() if not data["meta"].empty else {}
    feats["name"] = feats["code"].map(name_map).fillna(feats["code"])

    # 分数门槛
    th = SCORE_THRESHOLD_BOTTOM if regime == "阶段底" else SCORE_THRESHOLD_NORMAL
    feats["passed_threshold"] = feats["score"] >= th
    feats = feats.sort_values("score", ascending=False).reset_index(drop=True)
    feats["rank"] = feats.index + 1
    feats["regime"] = regime
    feats["asof_date"] = asof_date_str
    return feats.head(TOP_N * 2)


def compute_top_candidates(asof_date_str: str) -> pd.DataFrame:
    return _compute_top_candidates_uncached(asof_date_str)


# ============================================================
# 模拟持仓 + 调仓信号
# ============================================================
@st.cache_data(ttl=1800, show_spinner="生成调仓信号...")
def compute_rebalance_signal(asof_date_str: str, top_candidates_df: pd.DataFrame) -> dict:
    """今日调仓信号 (单日版本, 不模拟历史)

    原则:
      - 假设 上一交易日 收盘后 跑策略, 选出 Top N
      - "今日" asof 开盘: 应该买哪些, 卖哪些
      - 为简化, 这里直接展示 asof 当日的 Top 30 作为"今日应进"
        以及: 持仓股中分数 < 阈值 / 不在 Top 30 → "今日应出"

    输入: top_candidates_df (来自 compute_top_candidates)
    返回:
      - current_holdings: 模拟持仓 (基于"过去 30 日是否在 Top 30")
      - exits_today: 今日应卖
      - entries_today: 今日应买
    """
    data = _load_all_stocks()
    pool = data["pool"]
    if not pool or top_candidates_df.empty:
        return {"error": "数据缺失"}

    asof_date = pd.Timestamp(asof_date_str)
    top_codes = set(top_candidates_df.head(TOP_N)["code"].tolist())
    full_codes = set(top_candidates_df["code"].tolist())  # 通过阈值的全集

    # 模拟"过去 30 天"在 Top 30 出现 ≥ 15 天的代码 = 当前持仓
    # 为避免跑 30 天循环, 用一个简化的"近期 30 日平均分数"代理
    bench = _load_benchmark()
    bench_cut = bench[bench["date"] <= asof_date].reset_index(drop=True)
    if len(bench_cut) < 30:
        return {"error": "benchmark 数据不足"}
    recent_30_dates = bench_cut["date"].iloc[-30:].tolist()
    # 每只股票计算: 过去 30 日里, 收盘价相对 30 日前涨幅 > 0 (粗略"被持有过"代理)
    # 实际上, 简化为: 今日在 Top 30 且不在最近 5 日新高回撤的 → 模拟为"持仓中"
    hold_rows = []
    for _, r in top_candidates_df.head(TOP_N).iterrows():
        code = r["code"]
        df = pool.get(code)
        if df is None:
            continue
        d_df = df[df["date"] <= asof_date]
        if d_df.empty or len(d_df) < 30:
            continue
        close = d_df["close"]
        cp = float(close.iloc[-1])
        # 假设"30 日前"是入场日
        entry_idx = max(0, len(close) - 30)
        entry_price = float(close.iloc[entry_idx])
        max_price = float(close.iloc[entry_idx:].max())
        if max_price <= 0:
            continue
        mdd = cp / max_price - 1
        pnl = cp / entry_price - 1 if entry_price > 0 else 0
        # 只保留 mdd > -10% 的 (未触发止损)
        if mdd <= -STOP_DRAWDOWN:
            continue
        hold_rows.append({
            "code": code,
            "name": r["name"],
            "entry_date": str(d_df["date"].iloc[entry_idx])[:10],
            "entry_price": round(entry_price, 2),
            "current_price": round(cp, 2),
            "max_dd": round(mdd * 100, 1),
            "pnl_pct": round(pnl * 100, 1),
            "score": round(float(r["score"]), 1),
        })
    current = pd.DataFrame(hold_rows).sort_values("score", ascending=False) if hold_rows else pd.DataFrame()
    hold_codes = set(current["code"].tolist()) if not current.empty else set()

    # 今日应买: Top 30 中未持仓的 (按分数)
    entries = []
    for _, r in top_candidates_df.head(TOP_N).iterrows():
        if r["code"] not in hold_codes:
            entries.append({
                "code": r["code"], "name": r["name"],
                "close": float(r["close"]),
                "score": round(float(r["score"]), 1),
                "alpha_20": round(float(r["alpha_20"]) * 100, 1),
            })

    # 今日应卖: 持仓中分数 < 50 或不在 Top 30 (次低优先级触发)
    exits = []
    for _, h in current.iterrows():
        in_top = h["code"] in top_codes
        if not in_top and h["score"] < 50:
            exits.append({
                "code": h["code"], "name": h["name"],
                "reason": "分数<50 & 不在Top30",
                "score": h["score"],
            })

    return {
        "asof_date": asof_date_str,
        "current_holdings": current,
        "n_holdings": len(current),
        "entries_today": pd.DataFrame(entries),
        "exits_today": pd.DataFrame(exits),
    }


# ============================================================
# v2: 回测曲线 (N 日逐日 Top 30 等权 + 止损 + 调仓)
# ============================================================
def _build_daily_score_panel(panel: pd.DataFrame, asof_dates: list, bench: pd.DataFrame) -> pd.DataFrame:
    """一次性算出所有 asof 每天的 Top 30 分数 (向量化)

    返回 long DataFrame: columns=[date, code, close, score]
    每个 asof date 计算该日收盘后的 5/10/20 日α + MA200 + 分数
    """
    rows = []
    for asof in asof_dates:
        asof_ts = pd.Timestamp(asof)
        try:
            bench_cut = bench[bench["date"] <= asof_ts]
            if len(bench_cut) < 60:
                continue
            b5 = _alpha(bench_cut["close"], 5)
            b10 = _alpha(bench_cut["close"], 10)
            b20 = _alpha(bench_cut["close"], 20)
            b_close = float(bench_cut["close"].iloc[-1])
            b_ma20 = _ma(bench_cut["close"], 20)
            b_slope = _ma_slope(bench_cut["close"], 20)
        except Exception as e:
            print(f"[DEBUG] skip {asof_ts}: {e}", flush=True)
            continue
        # 大盘状态
        if b_close > b_ma20 and b_slope > 0.005:
            regime = "强势"
        elif b_close < b_ma20 and b_slope < -0.005:
            regime = "弱势"
        else:
            regime = "横盘"

        panel_cut = panel[panel["date"] <= asof_ts]
        # 每只取最后 220 行
        last_n = MA200_PERIOD + 20
        panel_recent = panel_cut.groupby("code").tail(last_n)

        def _per_code(s):
            s = s.sort_values("date")
            c = s["close"]
            n = len(c)
            # 所有 return 必须有同样的字段, 否则 groupby.apply 返回 Series 而不是 DataFrame
            empty = {"close": np.nan, "ma200": np.nan,
                      "low60": np.nan, "high60": np.nan,
                      "low10": np.nan, "high10": np.nan,
                      "alpha_10_now": np.nan, "alpha_10_prev": np.nan,
                      "alpha_5": np.nan, "alpha_10": np.nan, "alpha_20": np.nan}
            if n < MIN_LIST_DAYS:
                return pd.Series(empty)
            ma200 = float(c.iloc[-MA200_PERIOD:].mean())
            if np.isnan(ma200) or c.iloc[-1] <= ma200:
                return pd.Series({**empty, "close": float(c.iloc[-1]), "ma200": float("nan")})
            def _sp(k):
                if len(c) < k + 1:
                    return 0.0
                return float(c.iloc[-1] / c.iloc[-(k + 1)] - 1)
            low60 = float(c.iloc[-60:].min()) if len(c) >= 60 else float("nan")
            high60 = float(c.iloc[-60:].max()) if len(c) >= 60 else float("nan")
            low10 = float(c.iloc[-10:].min()) if len(c) >= 10 else float("nan")
            high10 = float(c.iloc[-10:].max()) if len(c) >= 10 else float("nan")
            if len(c) >= 16:
                alpha_10_now = float(c.iloc[-1] / c.iloc[-11] - 1)
                alpha_10_prev = float(c.iloc[-6] / c.iloc[-16] - 1)
            else:
                alpha_10_now = float("nan")
                alpha_10_prev = float("nan")
            return pd.Series({
                "close": float(c.iloc[-1]),
                "ma200": ma200,
                "low60": low60,
                "high60": high60,
                "low10": low10,
                "high10": high10,
                "alpha_10_now": alpha_10_now,
                "alpha_10_prev": alpha_10_prev,
                "alpha_5": _sp(5) - b5,
                "alpha_10": _sp(10) - b10,
                "alpha_20": _sp(20) - b20,
            })

        feats = panel_recent.groupby("code").apply(_per_code, include_groups=False)
        if not isinstance(feats, pd.DataFrame) or feats.empty:
            continue
        feats = feats.reset_index()
        feats = feats[feats["ma200"].notna()]
        if feats.empty:
            continue
        # 排名 + 分数
        for w in ("alpha_5", "alpha_10", "alpha_20"):
            feats[f"rank_{w}"] = feats[w].rank(method="average", pct=True) * 100
        feats["score"] = (
            feats["rank_alpha_5"] * ALPHA_WEIGHTS[0]
            + feats["rank_alpha_10"] * ALPHA_WEIGHTS[1]
            + feats["rank_alpha_20"] * ALPHA_WEIGHTS[2]
        )
        # 见底加成: 跟 _compute_top_candidates_uncached 一致的完整 3 条件
        if regime == "弱势":
            amp10 = (feats["high10"] - feats["low10"]) / feats["low10"].clip(lower=0.01)
            amp60 = (feats["high60"] - feats["low60"]) / feats["low60"].clip(lower=0.01)
            cond1 = amp10 < amp60 * 0.5
            cond2 = feats["close"] > feats["low60"] * 1.05
            cond3 = feats["alpha_10_now"] > feats["alpha_10_prev"]
            n_cond = cond1.astype(int) + cond2.astype(int) + cond3.fillna(False).astype(int)
            seek = (n_cond >= 2).fillna(False)
            feats["score"] = feats["score"].clip(upper=85.0) + np.where(seek, SEEK_BOTTOM_BONUS, 0)
        feats["score"] = feats["score"].clip(upper=100.0)
        feats["date"] = asof_ts
        rows.append(feats[["date", "code", "close", "score"]])
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


@st.cache_data(ttl=1800, show_spinner="回测中...")
def compute_backtest_curve(
    start_date_str: str,
    end_date_str: str,
) -> dict:
    """从 start_date 到 end_date 逐日跑 Top 30 等权组合

    返回 {
        "dates": [...],
        "nav_strategy": [净值, 起始=1.0],
        "nav_benchmark": [净值],
        "metrics": {累计/年化/最大回撤/夏普/胜率/调仓次数},
        "daily_top": [每日 Top 30 名单],
    }
    """
    data = _load_all_stocks()
    panel = data["panel"]
    pool = data["pool"]
    if panel.empty:
        return {"error": "数据缺失"}
    bench = _load_benchmark()
    if bench.empty:
        return {"error": "benchmark 缺失"}

    start_date = pd.Timestamp(start_date_str)
    end_date = pd.Timestamp(end_date_str)
    bench_cut = bench[(bench["date"] >= start_date) & (bench["date"] <= end_date)].reset_index(drop=True)
    if len(bench_cut) < 30:
        return {"error": "回测区间太短"}

    # 1. 一次性算所有交易日 (含 prev) 的分数矩阵
    asof_dates = bench_cut["date"].tolist()  # 每 t 日用 t 日 asof
    # 减少 asof 数量: 每周一次 (约 5 个交易日)
    asof_dates_dedup = asof_dates[::5]  # 每 5 天调一次
    if asof_dates[-1] not in asof_dates_dedup:
        asof_dates_dedup.append(asof_dates[-1])
    score_panel = _build_daily_score_panel(panel, asof_dates_dedup, bench)
    if score_panel.empty:
        return {"error": "分数计算失败"}

    # 2. 逐日跑回测: 用 score_panel 查表代替重算
    # score_panel: date, code, close, score
    # 取每天的 Top 30
    nav_strategy = [1.0]
    nav_benchmark = [1.0]
    dates = [bench_cut["date"].iloc[0]]
    holdings = {}
    rebalance_count = 0
    daily_top = []
    # 归因: 逐只累计贡献 (sum of daily contribution)
    # contribution[code] = {"total": float, "days": int, "alpha_sum": float, "name": str}
    contribution = {}
    # 查表: 用 searchsorted 或 merge_asof
    score_panel_sorted = score_panel.sort_values("date").reset_index(drop=True)
    unique_dates = sorted(score_panel_sorted["date"].unique())

    def _get_top_for_date(d):
        """取 d 当日 (或最近) 的分数, 算 Top 30"""
        # 用 d 之前的最近一个 asof date
        valid = [ad for ad in unique_dates if ad <= d]
        if not valid:
            return pd.DataFrame()
        last_asof = max(valid)
        sub = score_panel_sorted[score_panel_sorted["date"] == last_asof]
        if sub.empty:
            return pd.DataFrame()
        return sub.sort_values("score", ascending=False).head(TOP_N)

    for i in range(1, len(bench_cut)):
        d = bench_cut["date"].iloc[i]
        prev_d = bench_cut["date"].iloc[i - 1]
        # 只在调仓日重新取 Top 30 (调仓间隔 = REBALANCE_INTERVAL_DAYS)
        is_rebalance_day = (i % REBALANCE_INTERVAL_DAYS == 0)
        if is_rebalance_day:
            top_df = _get_top_for_date(prev_d)
            if top_df.empty:
                top_today = []
            else:
                top_today = top_df["code"].tolist()
                daily_top.append({"date": d, "top": top_today})
                # 调仓次数 (只在调仓日 +1, 反映"出现调仓动作"不反映补只数)
                rebalance_count += 1
        else:
            # 非调仓日, 复用上次的 top
            top_today = daily_top[-1]["top"] if daily_top else []

        # 1. 检查持仓退出 (检查 max_price 是在下面算日收益时一并更新)
        to_exit = []
        for code, h in list(holdings.items()):
            df = pool.get(code)
            if df is None:
                del holdings[code]
                continue
            d_df = df[df["date"] <= d]
            if len(d_df) < 2:
                continue
            cp = float(d_df["close"].iloc[-1])
            if cp > h["max_price"]:
                h["max_price"] = cp
            dd = cp / h["max_price"] - 1
            if dd <= -STOP_DRAWDOWN:
                to_exit.append(code)
        for code in to_exit:
            holdings.pop(code, None)

        # 2. 补位: 说明书 4.4 — 当日止损则次日才补新
        # 实现: 用 has_exited_today 标记控制
        can_rebalance = not to_exit  # 有止损则今日不补
        if can_rebalance and len(holdings) < TOP_N and top_today:
            for code in top_today:
                if len(holdings) >= TOP_N:
                    break
                if code in holdings:
                    continue
                df = pool.get(code)
                if df is None:
                    continue
                d_df = df[df["date"] <= d]
                if d_df.empty:
                    continue
                cp = float(d_df["close"].iloc[-1])
                # 从 pool 中取 name
                d_df_full = df
                code_name = str(d_df_full["name"].iloc[-1]) if "name" in d_df_full.columns and len(d_df_full) > 0 else code
                holdings[code] = {
                    "entry_price": cp,
                    "max_price": cp,
                    "entry_date": d,
                    "name": code_name,
                }

        # 3. 算 t 日的策略收益 (日收益, 不是从入场价的累计收益)
        # 同时记录归因: 每只股票当日贡献 = (今收/昨收 - 1) / n_holdings
        if holdings:
            rets = []
            n_hold = len(holdings)
            for code, h in holdings.items():
                df = pool.get(code)
                if df is None:
                    continue
                # 取 t 日 和 t-1 日 的 close
                d_df = df[df["date"] <= d]
                if len(d_df) < 2:
                    continue
                cp = float(d_df["close"].iloc[-1])
                pp = float(d_df["close"].iloc[-2])  # prev close
                if pp <= 0:
                    continue
                day_stock_ret = cp / pp - 1
                rets.append(day_stock_ret)
                # 归因: 等权下每只贡献 = day_stock_ret / n_hold
                day_contrib = day_stock_ret / n_hold
                if code not in contribution:
                    contribution[code] = {
                        "total": 0.0, "days": 0, "entry_date": h["entry_date"],
                        "current_price": cp, "name": h.get("name", code),
                    }
                contribution[code]["total"] += day_contrib
                contribution[code]["days"] += 1
                contribution[code]["current_price"] = cp
                # 顺便更新最高价
                if cp > h["max_price"]:
                    h["max_price"] = cp
            day_ret = float(np.mean(rets)) if rets else 0.0
        else:
            day_ret = 0.0
        # 调仓当日扣 0.05% (只在有实际新开仓的调仓日扣除, 约 0.05% × 调仓频率)
        if is_rebalance_day and len(to_exit) + (TOP_N - len(holdings)) > 0:
            day_ret -= 0.0005
        nav_strategy.append(nav_strategy[-1] * (1 + day_ret))

        # 4. benchmark
        bench_d = bench_cut.iloc[i]
        bench_prev = bench_cut.iloc[i - 1]
        b_ret = float(bench_d["close"] / bench_prev["close"] - 1) if bench_prev["close"] > 0 else 0.0
        nav_benchmark.append(nav_benchmark[-1] * (1 + b_ret))
        dates.append(d)

    # 5. 指标
    nav_s = pd.Series(nav_strategy)
    nav_b = pd.Series(nav_benchmark)
    n_days = len(nav_s)
    total_ret_s = float(nav_s.iloc[-1] - 1)
    total_ret_b = float(nav_b.iloc[-1] - 1)
    years = max(n_days / 244.0, 0.01)
    ann_ret_s = (1 + total_ret_s) ** (1 / years) - 1 if total_ret_s > -1 else -1.0
    ann_ret_b = (1 + total_ret_b) ** (1 / years) - 1 if total_ret_b > -1 else -1.0
    cummax_s = nav_s.cummax()
    mdd_s = float(((nav_s - cummax_s) / cummax_s).min())
    cummax_b = nav_b.cummax()
    mdd_b = float(((nav_b - cummax_b) / cummax_b).min())
    daily_s = nav_s.pct_change().fillna(0)
    daily_b = nav_b.pct_change().fillna(0)
    sharpe_s = float(daily_s.mean() / daily_s.std() * (244 ** 0.5)) if daily_s.std() > 0 else 0.0
    win_rate = float((daily_s > daily_b).mean())

    # 6. 归因: 转为 DataFrame 排序
    contrib_rows = []
    for code, c in contribution.items():
        # 计算期初价格 (用 entry_date 当天 or 之前)
        df = pool.get(code)
        if df is None:
            continue
        ep = c.get("entry_date")
        e_df = df[df["date"] >= ep] if ep is not None else df
        if e_df.empty:
            continue
        ep_price = float(e_df["close"].iloc[0])
        cp_price = c["current_price"]
        pnl = (cp_price / ep_price - 1) if ep_price > 0 else 0.0
        contrib_rows.append({
            "code": code,
            "name": c["name"],
            "contribution": c["total"],
            "days_held": c["days"],
            "entry_date": str(ep)[:10] if ep is not None else "",
            "entry_price": round(ep_price, 2),
            "current_price": round(cp_price, 2),
            "pnl_pct": pnl,
        })
    contrib_df = pd.DataFrame(contrib_rows)
    if not contrib_df.empty:
        contrib_df = contrib_df.sort_values("contribution", ascending=False).reset_index(drop=True)
        contrib_df["rank"] = contrib_df.index + 1

    # 7. 分年度拆分
    yearly = {}
    if dates and len(dates) > 1:
        dates_arr = pd.to_datetime(dates)
        years_list = sorted(set(dates_arr.year.tolist()))
        for y in years_list:
            mask = dates_arr.year == y
            if mask.sum() < 5:
                continue
            nav_y = nav_s[mask].reset_index(drop=True)
            nav_b_y = nav_b[mask].reset_index(drop=True)
            if len(nav_y) < 2:
                continue
            # 归一化: 区间内第一天 = 1.0
            nav_y_norm = nav_y / nav_y.iloc[0]
            nav_b_y_norm = nav_b_y / nav_b_y.iloc[0]
            y_total = float(nav_y_norm.iloc[-1] - 1)
            y_b_total = float(nav_b_y_norm.iloc[-1] - 1)
            y_days = len(nav_y)
            y_years = max(y_days / 244.0, 0.01)
            y_ann = (1 + y_total) ** (1 / y_years) - 1 if y_total > -1 else -1.0
            y_b_ann = (1 + y_b_total) ** (1 / y_years) - 1 if y_b_total > -1 else -1.0
            y_mdd = float(((nav_y_norm - nav_y_norm.cummax()) / nav_y_norm.cummax()).min())
            y_daily = nav_y_norm.pct_change().fillna(0)
            y_daily_b = nav_b_y_norm.pct_change().fillna(0)
            y_sharpe = float(y_daily.mean() / y_daily.std() * (244 ** 0.5)) if y_daily.std() > 0 else 0.0
            y_win = float((y_daily > y_daily_b).mean())
            yearly[str(y)] = {
                "days": int(y_days),
                "total": y_total,
                "annual": y_ann,
                "mdd": y_mdd,
                "sharpe": y_sharpe,
                "win_rate": y_win,
                "bm_total": y_b_total,
                "bm_annual": y_b_ann,
                "excess": y_total - y_b_total,
                "start_date": str(dates_arr[mask][0])[:10],
                "end_date": str(dates_arr[mask][-1])[:10],
                "rebalance_count": int(sum(
                    1 for dt in daily_top
                    if pd.Timestamp(dt["date"]).year == y
                )),
            }

    return {
        "dates": [str(d.date()) if hasattr(d, "date") else str(d)[:10] for d in dates],
        "nav_strategy": [float(x) for x in nav_strategy],
        "nav_benchmark": [float(x) for x in nav_benchmark],
        "metrics": {
            "total_return": total_ret_s,
            "annual_return": ann_ret_s,
            "max_drawdown": mdd_s,
            "sharpe": sharpe_s,
            "win_rate": win_rate,
            "rebalance_count": rebalance_count,
            "n_days": n_days,
            "bm_total": total_ret_b,
            "bm_annual": ann_ret_b,
            "bm_mdd": mdd_b,
            "excess_return": total_ret_s - total_ret_b,
        },
        "daily_top": daily_top,
        "contribution": contrib_df,
        "yearly": yearly,
    }


def _backtest_chart_html(bt: dict, width: int = 1100, height: int = 360) -> str:
    """净值曲线 HTML (策略 vs 创业板指) — 纯 inline SVG"""
    if "error" in bt:
        return f'<div style="color:{TEXT_MUTED};padding:14px;background:{BG_PANEL};' \
               f'border:1px solid {BORDER};border-radius:8px;">回测错误: {bt["error"]}</div>'
    dates = bt["dates"]
    nav_s = bt["nav_strategy"]
    nav_b = bt["nav_benchmark"]
    if len(dates) < 2:
        return f'<div style="color:{TEXT_MUTED};padding:14px;background:{BG_PANEL};' \
               f'border:1px solid {BORDER};border-radius:8px;">数据不足</div>'

    # 画布参数
    pad_l, pad_r, pad_t, pad_b = 60, 30, 30, 40
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    # 归一化
    all_vals = nav_s + nav_b
    y_min = min(all_vals)
    y_max = max(all_vals)
    y_range = y_max - y_min if y_max > y_min else 0.01
    # 边界加点 padding
    y_min -= y_range * 0.05
    y_max += y_range * 0.05
    y_range = y_max - y_min

    n = len(dates)
    def to_x(i):
        return pad_l + (i / max(n - 1, 1)) * plot_w
    def to_y(v):
        return pad_t + (1 - (v - y_min) / y_range) * plot_h

    # 策略路径 (绿, ACCENT_DN = 涨色)
    path_s = " ".join([f"{to_x(i):.1f},{to_y(v):.1f}" for i, v in enumerate(nav_s)])
    # benchmark 路径 (中色)
    path_b = " ".join([f"{to_x(i):.1f},{to_y(v):.1f}" for i, v in enumerate(nav_b)])

    # Y 轴刻度 (5 个)
    y_ticks = [y_min + (y_range * i / 4) for i in range(5)]
    y_tick_labels = [f"{v:.2f}" for v in y_ticks]
    y_tick_lines = "".join([
        f'<line x1="{pad_l}" y1="{to_y(v):.1f}" x2="{pad_l + plot_w}" y2="{to_y(v):.1f}" '
        f'stroke="{BORDER}" stroke-width="0.5" stroke-dasharray="2,2"/>'
        f'<text x="{pad_l - 6}" y="{to_y(v):.1f}" text-anchor="end" '
        f'fill="{TEXT_DIM}" font-size="10" font-family="monospace" dominant-baseline="middle">{lbl}</text>'
        for v, lbl in zip(y_ticks, y_tick_labels)
    ])

    # X 轴刻度 (起、中、末)
    x_tick_idx = [0, n // 2, n - 1]
    x_tick_lines = "".join([
        f'<text x="{to_x(i):.1f}" y="{pad_t + plot_h + 18}" text-anchor="middle" '
        f'fill="{TEXT_DIM}" font-size="10" font-family="monospace">{dates[i]}</text>'
        for i in x_tick_idx
    ])

    # 初始水平线 (1.0)
    init_line = ""
    if y_min < 1.0 < y_max:
        init_line = (
            f'<line x1="{pad_l}" y1="{to_y(1.0):.1f}" x2="{pad_l + plot_w}" y2="{to_y(1.0):.1f}" '
            f'stroke="{TEXT_MUTED}" stroke-width="0.5" stroke-dasharray="4,3"/>'
        )

    # 路径
    poly_s = f'<polyline points="{path_s}" fill="none" stroke="{ACCENT_DN}" stroke-width="1.8" stroke-linejoin="round"/>'
    poly_b = f'<polyline points="{path_b}" fill="none" stroke="{TEXT_MUTED}" stroke-width="1.4" stroke-linejoin="round" stroke-dasharray="3,2"/>'

    # 图例
    legend_y = pad_t + 6
    legend = (
        f'<rect x="{pad_l + plot_w - 180}" y="{legend_y}" width="170" height="38" '
        f'fill="{BG_PANEL}" stroke="{BORDER}" rx="3"/>'
        f'<line x1="{pad_l + plot_w - 170}" y1="{legend_y + 12}" x2="{pad_l + plot_w - 145}" y2="{legend_y + 12}" '
        f'stroke="{ACCENT_DN}" stroke-width="2"/>'
        f'<text x="{pad_l + plot_w - 140}" y="{legend_y + 15}" fill="{TEXT}" font-size="11">策略净值</text>'
        f'<line x1="{pad_l + plot_w - 170}" y1="{legend_y + 28}" x2="{pad_l + plot_w - 145}" y2="{legend_y + 28}" '
        f'stroke="{TEXT_MUTED}" stroke-width="1.5" stroke-dasharray="3,2"/>'
        f'<text x="{pad_l + plot_w - 140}" y="{legend_y + 31}" fill="{TEXT}" font-size="11">创业板指</text>'
    )

    svg = (
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="background:{BG_PANEL};border:1px solid {BORDER};border-radius:8px;width:100%;height:auto;">'
        f'{y_tick_lines}'
        f'{init_line}'
        f'{poly_b}'
        f'{poly_s}'
        f'{legend}'
        f'{x_tick_lines}'
        f'</svg>'
    )
    return f'<div style="margin-bottom:12px;">{svg}</div>'


def _backtest_kpi_html(metrics: dict) -> str:
    """回测 KPI 卡: 策略累计/年化/回撤/夏普 vs 创业板指 累计/年化/回撤"""
    def fmt_pct(v):
        return f"{v*100:+.1f}%" if v is not None else "—"
    def fmt_val(v, digits=2):
        return f"{v:.{digits}f}" if v is not None else "—"

    m = metrics
    s_total = fmt_pct(m.get("total_return"))
    s_annual = fmt_pct(m.get("annual_return"))
    s_mdd = fmt_pct(m.get("max_drawdown"))
    s_sharpe = fmt_val(m.get("sharpe"))
    s_win = fmt_pct(m.get("win_rate"))
    s_rebal = str(m.get("rebalance_count", 0))
    b_total = fmt_pct(m.get("bm_total"))
    b_annual = fmt_pct(m.get("bm_annual"))
    b_mdd = fmt_pct(m.get("bm_mdd"))
    excess = fmt_pct(m.get("excess_return"))

    cards = (
        # 策略列
        kpi_card("策略累计收益", s_total, f"vs 创业板 {b_total}", ACCENT_DN)
        + kpi_card("策略年化", s_annual, f"vs 创业板 {b_annual}", ACCENT_DN)
        + kpi_card("策略最大回撤", s_mdd, f"创业板 {b_mdd}", ACCENT_UP)
        + kpi_card("夏普比率", s_sharpe, f"胜率 {s_win}", "#5b93e0")
        + kpi_card("超额收益", excess, f"调仓 {s_rebal} 次", "#ffc107")
    )
    return f'<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:6px 0 14px 0;">{cards}</div>'


# ============================================================
# v2 扩展: 归因 + 分年度渲染
# ============================================================
def _attribution_table_html(contrib_df: pd.DataFrame) -> str:
    """业绩归因: Top 10 贡献者 + Bottom 5 拖累者"""
    if contrib_df.empty:
        return f'<div style="color:{TEXT_MUTED};padding:14px;background:{BG_PANEL};' \
               f'border:1px solid {BORDER};border-radius:8px;text-align:center;">' \
               f'无归因数据</div>'

    # Top 10 贡献者
    top = contrib_df.head(10)
    top_rows = []
    for _, r in top.iterrows():
        contrib_color = ACCENT_DN if r["contribution"] > 0 else ACCENT_UP
        pnl_color = ACCENT_DN if r["pnl_pct"] > 0 else ACCENT_UP
        top_rows.append(
            f'<tr style="border-bottom:1px solid {BORDER};">'
            f'<td style="padding:5px 8px;color:{TEXT_MUTED};font-family:monospace;">{int(r["rank"])}</td>'
            f'<td style="padding:5px 8px;color:{TEXT};font-weight:600;">{r["code"]}</td>'
            f'<td style="padding:5px 8px;color:{TEXT};">{r["name"]}</td>'
            f'<td style="padding:5px 8px;color:{TEXT_MUTED};font-family:monospace;">{r["entry_date"]}</td>'
            f'<td style="padding:5px 8px;color:{contrib_color};font-family:monospace;font-weight:700;text-align:right;">{r["contribution"]*100:+.2f}%</td>'
            f'<td style="padding:5px 8px;color:{TEXT};font-family:monospace;text-align:right;">{r["entry_price"]:.2f}</td>'
            f'<td style="padding:5px 8px;color:{TEXT};font-family:monospace;text-align:right;">{r["current_price"]:.2f}</td>'
            f'<td style="padding:5px 8px;color:{pnl_color};font-family:monospace;text-align:right;">{r["pnl_pct"]*100:+.1f}%</td>'
            f'<td style="padding:5px 8px;color:{TEXT_MUTED};font-family:monospace;text-align:right;">{int(r["days_held"])}</td>'
            f'</tr>'
        )
    top_header = (
        f'<tr style="background:{BG_PANEL_HI};">'
        f'<th style="padding:6px 8px;text-align:left;color:{TEXT_MUTED};font-size:11px;">#</th>'
        f'<th style="padding:6px 8px;text-align:left;color:{TEXT_MUTED};font-size:11px;">代码</th>'
        f'<th style="padding:6px 8px;text-align:left;color:{TEXT_MUTED};font-size:11px;">名称</th>'
        f'<th style="padding:6px 8px;text-align:left;color:{TEXT_MUTED};font-size:11px;">入场日</th>'
        f'<th style="padding:6px 8px;text-align:right;color:{ACCENT_DN};font-size:11px;">贡献%</th>'
        f'<th style="padding:6px 8px;text-align:right;color:{TEXT_MUTED};font-size:11px;">入场价</th>'
        f'<th style="padding:6px 8px;text-align:right;color:{TEXT_MUTED};font-size:11px;">现价</th>'
        f'<th style="padding:6px 8px;text-align:right;color:{TEXT_MUTED};font-size:11px;">收益%</th>'
        f'<th style="padding:6px 8px;text-align:right;color:{TEXT_MUTED};font-size:11px;">持仓天数</th>'
        f'</tr>'
    )
    top_html = (
        f'<div style="background:{BG_PANEL};border:1px solid {BORDER};border-radius:8px;overflow:hidden;">'
        f'<div style="padding:8px 12px;background:{BG_PANEL_HI};border-bottom:1px solid {BORDER};">'
        f'<span style="color:{ACCENT_DN};font-size:13px;font-weight:600;">🏆 Top 10 贡献者</span>'
        f'<span style="color:{TEXT_MUTED};font-size:11px;margin-left:8px;">贡献 = 每日收益 / 持仓数 累计</span>'
        f'</div>'
        f'<table style="width:100%;border-collapse:collapse;font-size:12px;">{top_header}{"".join(top_rows)}</table>'
        f'</div>'
    )

    # Bottom 5 拖累者
    bottom = contrib_df.tail(5).iloc[::-1]  # 倒数 5 个
    bottom_rows = []
    for _, r in bottom.iterrows():
        contrib_color = ACCENT_DN if r["contribution"] > 0 else ACCENT_UP
        pnl_color = ACCENT_DN if r["pnl_pct"] > 0 else ACCENT_UP
        bottom_rows.append(
            f'<tr style="border-bottom:1px solid {BORDER};">'
            f'<td style="padding:5px 8px;color:{TEXT_MUTED};font-family:monospace;">{int(r["rank"])}</td>'
            f'<td style="padding:5px 8px;color:{TEXT};font-weight:600;">{r["code"]}</td>'
            f'<td style="padding:5px 8px;color:{TEXT};">{r["name"]}</td>'
            f'<td style="padding:5px 8px;color:{TEXT_MUTED};font-family:monospace;">{r["entry_date"]}</td>'
            f'<td style="padding:5px 8px;color:{contrib_color};font-family:monospace;font-weight:700;text-align:right;">{r["contribution"]*100:+.2f}%</td>'
            f'<td style="padding:5px 8px;color:{TEXT};font-family:monospace;text-align:right;">{r["entry_price"]:.2f}</td>'
            f'<td style="padding:5px 8px;color:{TEXT};font-family:monospace;text-align:right;">{r["current_price"]:.2f}</td>'
            f'<td style="padding:5px 8px;color:{pnl_color};font-family:monospace;text-align:right;">{r["pnl_pct"]*100:+.1f}%</td>'
            f'<td style="padding:5px 8px;color:{TEXT_MUTED};font-family:monospace;text-align:right;">{int(r["days_held"])}</td>'
            f'</tr>'
        )
    bottom_header = top_header.replace(f'color:{ACCENT_DN}', f'color:{ACCENT_UP}').replace('贡献%', '拖累%')
    bottom_html = (
        f'<div style="background:{BG_PANEL};border:1px solid {BORDER};border-radius:8px;overflow:hidden;">'
        f'<div style="padding:8px 12px;background:{BG_PANEL_HI};border-bottom:1px solid {BORDER};">'
        f'<span style="color:{ACCENT_UP};font-size:13px;font-weight:600;">📉 Bottom 5 拖累者</span>'
        f'<span style="color:{TEXT_MUTED};font-size:11px;margin-left:8px;">贡献最低的 5 只</span>'
        f'</div>'
        f'<table style="width:100%;border-collapse:collapse;font-size:12px;">{bottom_header}{"".join(bottom_rows)}</table>'
        f'</div>'
    )

    return f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px;">{top_html}{bottom_html}</div>'


def _yearly_compare_html(yearly: dict) -> str:
    """分年度对比表 + 柱状图"""
    if not yearly:
        return f'<div style="color:{TEXT_MUTED};padding:14px;background:{BG_PANEL};' \
               f'border:1px solid {BORDER};border-radius:8px;text-align:center;">' \
               f'无分年度数据</div>'

    # 按年份排序
    years_sorted = sorted(yearly.keys())
    # 表格
    header_cells = "".join([
        f'<th style="padding:8px 12px;text-align:right;color:{TEXT_MUTED};font-size:11px;font-weight:500;">{y}</th>'
        for y in years_sorted
    ])
    # 各行
    rows_def = [
        ("区间", lambda y: f"{yearly[y]['start_date']} → {yearly[y]['end_date']}", TEXT_MUTED),
        ("天数", lambda y: f"{yearly[y]['days']}", TEXT),
        ("调仓次数", lambda y: f"{yearly[y].get('rebalance_count', 0)} 次", "#5b93e0"),
        ("策略累计", lambda y: f"{yearly[y]['total']*100:+.1f}%", ACCENT_DN),
        ("策略年化", lambda y: f"{yearly[y]['annual']*100:+.1f}%", ACCENT_DN),
        ("最大回撤", lambda y: f"{yearly[y]['mdd']*100:+.1f}%", ACCENT_UP),
        ("夏普", lambda y: f"{yearly[y]['sharpe']:.2f}", TEXT),
        ("胜率", lambda y: f"{yearly[y]['win_rate']*100:.1f}%", TEXT),
        ("创业板累计", lambda y: f"{yearly[y]['bm_total']*100:+.1f}%", TEXT_MUTED),
        ("创业板年化", lambda y: f"{yearly[y]['bm_annual']*100:+.1f}%", TEXT_MUTED),
        ("超额收益", lambda y: f"{yearly[y]['excess']*100:+.1f}%", "#5b93e0"),
    ]
    body_rows = []
    for label, fn, color in rows_def:
        cells = "".join([
            f'<td style="padding:6px 12px;color:{color};font-family:monospace;text-align:right;font-size:12px;">{fn(y)}</td>'
            for y in years_sorted
        ])
        body_rows.append(
            f'<tr style="border-bottom:1px solid {BORDER};">'
            f'<td style="padding:6px 12px;color:{TEXT_MUTED};font-size:11px;font-weight:500;">{label}</td>'
            f'{cells}</tr>'
        )
    table_html = (
        f'<div style="background:{BG_PANEL};border:1px solid {BORDER};border-radius:8px;overflow:hidden;margin-bottom:14px;">'
        f'<div style="padding:8px 12px;background:{BG_PANEL_HI};border-bottom:1px solid {BORDER};">'
        f'<span style="color:{TEXT};font-size:13px;font-weight:600;">📅 分年度业绩对比</span>'
        f'<span style="color:{TEXT_MUTED};font-size:11px;margin-left:8px;">各年度独立计算 (区间内首日净值 = 1.0)</span>'
        f'</div>'
        f'<table style="width:100%;border-collapse:collapse;">'
        f'<thead><tr style="background:{BG_PANEL_HI};">'
        f'<th style="padding:8px 12px;text-align:left;color:{TEXT_MUTED};font-size:11px;font-weight:500;">指标</th>'
        f'{header_cells}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody>'
        f'</table></div>'
    )

    # 柱状图: 策略年化 vs 创业板年化
    bar_html = _yearly_bar_chart_html(yearly, years_sorted)
    return table_html + bar_html


def _yearly_bar_chart_html(yearly: dict, years_sorted: list) -> str:
    """分年度柱状图 (策略 vs 创业板) — 纯 SVG"""
    if not years_sorted:
        return ""
    width = 600
    height = 220
    pad_l, pad_r, pad_t, pad_b = 50, 20, 30, 50
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    n = len(years_sorted)
    group_w = plot_w / n
    bar_w = group_w * 0.35
    # 找 max abs value
    all_vals = []
    for y in years_sorted:
        all_vals.append(yearly[y]["annual"])
        all_vals.append(yearly[y]["bm_annual"])
    max_abs = max(abs(v) for v in all_vals) if all_vals else 0.1
    y_max = max_abs * 1.1
    y_min = -max_abs * 0.3 if any(v < 0 for v in all_vals) else 0
    y_range = y_max - y_min if y_max > y_min else 0.01

    def to_y(v):
        return pad_t + (1 - (v - y_min) / y_range) * plot_h
    zero_y = to_y(0)

    bars = []
    for i, y in enumerate(years_sorted):
        x_center = pad_l + i * group_w + group_w / 2
        # 策略柱
        s_val = yearly[y]["annual"]
        s_y = to_y(s_val)
        s_top = min(s_y, zero_y)
        s_h = abs(zero_y - s_y)
        s_color = ACCENT_DN if s_val > 0 else ACCENT_UP
        bars.append(
            f'<rect x="{x_center - bar_w - 2:.1f}" y="{s_top:.1f}" width="{bar_w:.1f}" height="{s_h:.1f}" '
            f'fill="{s_color}" opacity="0.85">'
            f'<title>{y} 策略年化: {s_val*100:+.1f}%</title></rect>'
            f'<text x="{x_center - bar_w/2 - 2:.1f}" y="{s_top - 4:.1f}" text-anchor="middle" '
            f'fill="{s_color}" font-size="10" font-family="monospace">{s_val*100:+.0f}%</text>'
        )
        # 创业板柱
        b_val = yearly[y]["bm_annual"]
        b_y = to_y(b_val)
        b_top = min(b_y, zero_y)
        b_h = abs(zero_y - b_y)
        bars.append(
            f'<rect x="{x_center + 2:.1f}" y="{b_top:.1f}" width="{bar_w:.1f}" height="{b_h:.1f}" '
            f'fill="{TEXT_MUTED}" opacity="0.6">'
            f'<title>{y} 创业板年化: {b_val*100:+.1f}%</title></rect>'
            f'<text x="{x_center + bar_w/2 + 2:.1f}" y="{b_top - 4:.1f}" text-anchor="middle" '
            f'fill="{TEXT_MUTED}" font-size="10" font-family="monospace">{b_val*100:+.0f}%</text>'
        )
        # 年份标签
        bars.append(
            f'<text x="{x_center:.1f}" y="{pad_t + plot_h + 16:.1f}" text-anchor="middle" '
            f'fill="{TEXT}" font-size="11" font-weight="600">{y}</text>'
        )

    # 零线
    zero_line = (
        f'<line x1="{pad_l}" y1="{zero_y:.1f}" x2="{pad_l + plot_w}" y2="{zero_y:.1f}" '
        f'stroke="{BORDER_HI}" stroke-width="0.8"/>'
    )
    # Y 轴标签
    y_labels = []
    for v in [y_min, 0, y_max]:
        if y_min <= v <= y_max:
            y_labels.append(
                f'<text x="{pad_l - 6}" y="{to_y(v):.1f}" text-anchor="end" '
                f'fill="{TEXT_DIM}" font-size="10" font-family="monospace" dominant-baseline="middle">{v*100:+.0f}%</text>'
            )

    # 图例
    legend = (
        f'<rect x="{pad_l + plot_w - 160}" y="{pad_t - 4}" width="150" height="22" '
        f'fill="{BG_PANEL}" stroke="{BORDER}" rx="3"/>'
        f'<rect x="{pad_l + plot_w - 152}" y="{pad_t + 4}" width="10" height="10" fill="{ACCENT_DN}"/>'
        f'<text x="{pad_l + plot_w - 138}" y="{pad_t + 12}" fill="{TEXT}" font-size="11">策略</text>'
        f'<rect x="{pad_l + plot_w - 100}" y="{pad_t + 4}" width="10" height="10" fill="{TEXT_MUTED}" opacity="0.6"/>'
        f'<text x="{pad_l + plot_w - 86}" y="{pad_t + 12}" fill="{TEXT}" font-size="11">创业板</text>'
    )

    svg = (
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="background:{BG_PANEL};border:1px solid {BORDER};border-radius:8px;width:100%;height:auto;">'
        f'{"".join(y_labels)}{zero_line}{"".join(bars)}{legend}'
        f'</svg>'
    )
    return f'<div style="margin-bottom:16px;">{svg}</div>'


# ============================================================
# 渲染
# ============================================================
def _format_pct(v: float, digits: int = 1) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v:+.{digits}f}%"


def _regime_card_html(regime: str, position_ratio: float, n_candidates: int, n_holdings: int) -> str:
    color = REGIME_COLORS.get(regime, TEXT_MUTED)
    cards = (
        kpi_card("大盘状态", regime, f"仓位 {position_ratio*100:.0f}%", color)
        + kpi_card("建议仓位", f"{position_ratio*100:.0f}%", f"{position_ratio*100:.0f}% × 30只", ACCENT_UP)
        + kpi_card("候选池", f"{n_candidates}", f"Top {TOP_N} 候选", "#5b93e0")
        + kpi_card("模拟持仓", f"{n_holdings}", f"/ {TOP_N} 只 (非实盘)", "#ffc107")
    )
    return f'<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:6px 0 14px 0;">{cards}</div>'


def _regime_strip_html(regime_df: pd.DataFrame) -> str:
    """最近 N 日大盘状态色条"""
    if regime_df.empty:
        return ""
    last_n = regime_df.tail(30)
    cells = []
    for _, r in last_n.iterrows():
        c = REGIME_COLORS.get(r["regime"], TEXT_MUTED)
        d = str(r["date"])[:10]
        cells.append(
            f'<div title="{d} · {r["regime"]}" '
            f'style="flex:1;height:24px;background:{c};border-radius:2px;'
            f'opacity:0.85;cursor:default;margin-right:1px;"></div>'
        )
    html = (
        '<div style="display:flex;align-items:center;margin:6px 0 4px 0;">'
        '<div style="color:#7a7f96;font-size:11px;width:80px;flex-shrink:0;">'
        '最近 30 日:</div>'
        f'<div style="display:flex;flex:1;align-items:center;">{"".join(cells)}</div>'
        '</div>'
    )
    # 图例
    legend = "".join([
        f'<span style="display:inline-block;width:10px;height:10px;background:{c};'
        f'border-radius:2px;margin-right:4px;vertical-align:middle;"></span>'
        f'<span style="color:{TEXT_MUTED};font-size:11px;margin-right:12px;">{name}</span>'
        for name, c in REGIME_COLORS.items()
    ])
    return html + f'<div style="margin:2px 0 14px 80px;">{legend}</div>'


def _candidates_table_html(df: pd.DataFrame, top_n: int = TOP_N) -> str:
    if df.empty:
        return f'<div style="color:{TEXT_MUTED};padding:20px;text-align:center;">无候选数据</div>'
    df_view = df.head(top_n).copy()
    rows = []
    for _, r in df_view.iterrows():
        score_color = ACCENT_UP if r["score"] >= 75 else (TEXT if r["score"] >= 60 else TEXT_MUTED)
        a5_color = ACCENT_UP if r["alpha_5"] > 0 else ACCENT_DN
        a10_color = ACCENT_UP if r["alpha_10"] > 0 else ACCENT_DN
        a20_color = ACCENT_UP if r["alpha_20"] > 0 else ACCENT_DN
        seek_badge = (
            '<span style="background:#ffd54f;color:#0a0e1a;padding:1px 6px;'
            'border-radius:4px;font-size:10px;font-weight:600;">见底+15</span>'
            if r.get("seek_bottom", False) else ""
        )
        rows.append(
            f'<tr style="border-bottom:1px solid {BORDER};">'
            f'<td style="padding:6px 8px;color:{TEXT_MUTED};font-family:monospace;width:30px;">{int(r["rank"])}</td>'
            f'<td style="padding:6px 8px;color:{TEXT};font-weight:600;">{r["code"]}</td>'
            f'<td style="padding:6px 8px;color:{TEXT};">{r["name"]}</td>'
            f'<td style="padding:6px 8px;color:{TEXT};font-family:monospace;text-align:right;">{r["close"]:.2f}</td>'
            f'<td style="padding:6px 8px;color:{a5_color};font-family:monospace;text-align:right;">{_format_pct(r["alpha_5"]*100)}</td>'
            f'<td style="padding:6px 8px;color:{a10_color};font-family:monospace;text-align:right;">{_format_pct(r["alpha_10"]*100)}</td>'
            f'<td style="padding:6px 8px;color:{a20_color};font-family:monospace;text-align:right;">{_format_pct(r["alpha_20"]*100)}</td>'
            f'<td style="padding:6px 8px;color:{score_color};font-family:monospace;font-weight:700;text-align:right;">{r["score"]:.1f}</td>'
            f'<td style="padding:6px 8px;text-align:center;">{seek_badge}</td>'
            f'</tr>'
        )
    header = (
        f'<tr style="background:{BG_PANEL_HI};border-bottom:1px solid {BORDER_HI};">'
        f'<th style="padding:8px;text-align:left;color:{TEXT_MUTED};font-size:11px;font-weight:500;">#</th>'
        f'<th style="padding:8px;text-align:left;color:{TEXT_MUTED};font-size:11px;font-weight:500;">代码</th>'
        f'<th style="padding:8px;text-align:left;color:{TEXT_MUTED};font-size:11px;font-weight:500;">名称</th>'
        f'<th style="padding:8px;text-align:right;color:{TEXT_MUTED};font-size:11px;font-weight:500;">现价</th>'
        f'<th style="padding:8px;text-align:right;color:{TEXT_MUTED};font-size:11px;font-weight:500;">5日α</th>'
        f'<th style="padding:8px;text-align:right;color:{TEXT_MUTED};font-size:11px;font-weight:500;">10日α</th>'
        f'<th style="padding:8px;text-align:right;color:{TEXT_MUTED};font-size:11px;font-weight:500;">20日α</th>'
        f'<th style="padding:8px;text-align:right;color:{TEXT_MUTED};font-size:11px;font-weight:500;">分数</th>'
        f'<th style="padding:8px;text-align:center;color:{TEXT_MUTED};font-size:11px;font-weight:500;">加成</th>'
        f'</tr>'
    )
    return (
        f'<div style="background:{BG_PANEL};border:1px solid {BORDER};border-radius:8px;'
        f'overflow:hidden;margin-bottom:16px;">'
        f'<div style="padding:10px 14px;background:{BG_PANEL_HI};border-bottom:1px solid {BORDER};">'
        f'<span style="color:{TEXT};font-size:13px;font-weight:600;">📊 候选池 Top {top_n}</span>'
        f'<span style="color:{TEXT_MUTED};font-size:11px;margin-left:8px;">(MA200 过滤 · 分数门槛 60)</span>'
        f'</div>'
        f'<table style="width:100%;border-collapse:collapse;font-size:12px;">{header}{"".join(rows)}</table>'
        f'</div>'
    )


def _rebalance_signal_html(signal: dict) -> str:
    """今日调仓信号: 应买 + 应卖"""
    if "error" in signal:
        return ""
    entries = signal.get("entries_today", pd.DataFrame())
    exits = signal.get("exits_today", pd.DataFrame())
    e_html = ""
    if not entries.empty:
        rows_e = []
        for _, r in entries.head(15).iterrows():
            rows_e.append(
                f'<tr style="border-bottom:1px solid {BORDER};">'
                f'<td style="padding:5px 8px;color:{ACCENT_UP};font-weight:600;">↗ {r["code"]}</td>'
                f'<td style="padding:5px 8px;color:{TEXT};">{r["name"]}</td>'
                f'<td style="padding:5px 8px;color:{TEXT};font-family:monospace;text-align:right;">{r["close"]:.2f}</td>'
                f'<td style="padding:5px 8px;color:{TEXT};font-family:monospace;text-align:right;">{r["score"]:.1f}</td>'
                f'<td style="padding:5px 8px;color:{ACCENT_UP};font-family:monospace;text-align:right;">{r["alpha_20"]:+.1f}%</td>'
                f'</tr>'
            )
        header_e = (
            f'<tr style="background:{BG_PANEL_HI};">'
            f'<th style="padding:6px 8px;text-align:left;color:{ACCENT_UP};font-size:11px;">代码</th>'
            f'<th style="padding:6px 8px;text-align:left;color:{TEXT_MUTED};font-size:11px;">名称</th>'
            f'<th style="padding:6px 8px;text-align:right;color:{TEXT_MUTED};font-size:11px;">现价</th>'
            f'<th style="padding:6px 8px;text-align:right;color:{TEXT_MUTED};font-size:11px;">分数</th>'
            f'<th style="padding:6px 8px;text-align:right;color:{TEXT_MUTED};font-size:11px;">20日α</th>'
            f'</tr>'
        )
        e_html = (
            f'<div style="background:{BG_PANEL};border:1px solid {BORDER};border-radius:8px;overflow:hidden;">'
            f'<div style="padding:8px 12px;background:{BG_PANEL_HI};border-bottom:1px solid {BORDER};">'
            f'<span style="color:{ACCENT_UP};font-size:13px;font-weight:600;">📗 今日应买 (从 Top 30 中未持仓)</span>'
            f'<span style="color:{TEXT_MUTED};font-size:11px;margin-left:8px;">共 {len(entries)} 只</span>'
            f'</div>'
            f'<table style="width:100%;border-collapse:collapse;font-size:12px;">{header_e}{"".join(rows_e)}</table>'
            f'</div>'
        )
    x_html = ""
    if not exits.empty:
        rows_x = []
        for _, r in exits.iterrows():
            rows_x.append(
                f'<tr style="border-bottom:1px solid {BORDER};">'
                f'<td style="padding:5px 8px;color:{ACCENT_DN};font-weight:600;">↘ {r["code"]}</td>'
                f'<td style="padding:5px 8px;color:{TEXT};">{r["name"]}</td>'
                f'<td style="padding:5px 8px;color:{TEXT_DIM};font-size:11px;">{r["reason"]}</td>'
                f'<td style="padding:5px 8px;color:{TEXT};font-family:monospace;text-align:right;">{r["score"]:.1f}</td>'
                f'</tr>'
            )
        header_x = (
            f'<tr style="background:{BG_PANEL_HI};">'
            f'<th style="padding:6px 8px;text-align:left;color:{ACCENT_DN};font-size:11px;">代码</th>'
            f'<th style="padding:6px 8px;text-align:left;color:{TEXT_MUTED};font-size:11px;">名称</th>'
            f'<th style="padding:6px 8px;text-align:left;color:{TEXT_MUTED};font-size:11px;">原因</th>'
            f'<th style="padding:6px 8px;text-align:right;color:{TEXT_MUTED};font-size:11px;">分数</th>'
            f'</tr>'
        )
        x_html = (
            f'<div style="background:{BG_PANEL};border:1px solid {BORDER};border-radius:8px;overflow:hidden;">'
            f'<div style="padding:8px 12px;background:{BG_PANEL_HI};border-bottom:1px solid {BORDER};">'
            f'<span style="color:{ACCENT_DN};font-size:13px;font-weight:600;">📕 今日应卖 (不触发止损但分数低)</span>'
            f'<span style="color:{TEXT_MUTED};font-size:11px;margin-left:8px;">共 {len(exits)} 只</span>'
            f'</div>'
            f'<table style="width:100%;border-collapse:collapse;font-size:12px;">{header_x}{"".join(rows_x)}</table>'
            f'</div>'
        )
    if not e_html and not x_html:
        return f'<div style="color:{TEXT_MUTED};padding:12px;background:{BG_PANEL};' \
               f'border:1px solid {BORDER};border-radius:8px;text-align:center;margin-bottom:16px;">' \
               f'今日无调仓信号 — 持仓稳定</div>'
    # 两列布局
    if e_html and x_html:
        return f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px;">{e_html}{x_html}</div>'
    return f'<div style="margin-bottom:16px;">{e_html}{x_html}</div>'


def _holdings_table_html(df: pd.DataFrame) -> str:
    if df.empty:
        return f'<div style="color:{TEXT_MUTED};padding:14px;background:{BG_PANEL};' \
               f'border:1px solid {BORDER};border-radius:8px;text-align:center;">' \
               f'当前无持仓 (近 60 日未选出可入场标的)</div>'
    rows = []
    for _, r in df.iterrows():
        pnl_color = ACCENT_UP if r["pnl_pct"] > 0 else ACCENT_DN
        mdd_color = ACCENT_DN if r["max_dd"] <= -10 else (TEXT if r["max_dd"] <= -5 else TEXT_MUTED)
        rows.append(
            f'<tr style="border-bottom:1px solid {BORDER};">'
            f'<td style="padding:6px 8px;color:{TEXT};font-weight:600;">{r["code"]}</td>'
            f'<td style="padding:6px 8px;color:{TEXT};">{r["name"]}</td>'
            f'<td style="padding:6px 8px;color:{TEXT_MUTED};font-family:monospace;">{r["entry_date"]}</td>'
            f'<td style="padding:6px 8px;color:{TEXT};font-family:monospace;text-align:right;">{r["entry_price"]:.2f}</td>'
            f'<td style="padding:6px 8px;color:{TEXT};font-family:monospace;text-align:right;">{r["current_price"]:.2f}</td>'
            f'<td style="padding:6px 8px;color:{pnl_color};font-family:monospace;font-weight:600;text-align:right;">{r["pnl_pct"]:+.1f}%</td>'
            f'<td style="padding:6px 8px;color:{mdd_color};font-family:monospace;text-align:right;">{r["max_dd"]:+.1f}%</td>'
            f'<td style="padding:6px 8px;color:{TEXT};font-family:monospace;text-align:right;">{r["score"]:.1f}</td>'
            f'</tr>'
        )
    header = (
        f'<tr style="background:{BG_PANEL_HI};border-bottom:1px solid {BORDER_HI};">'
        f'<th style="padding:8px;text-align:left;color:{TEXT_MUTED};font-size:11px;font-weight:500;">代码</th>'
        f'<th style="padding:8px;text-align:left;color:{TEXT_MUTED};font-size:11px;font-weight:500;">名称</th>'
        f'<th style="padding:8px;text-align:left;color:{TEXT_MUTED};font-size:11px;font-weight:500;">入场日</th>'
        f'<th style="padding:8px;text-align:right;color:{TEXT_MUTED};font-size:11px;font-weight:500;">入场价</th>'
        f'<th style="padding:8px;text-align:right;color:{TEXT_MUTED};font-size:11px;font-weight:500;">现价</th>'
        f'<th style="padding:8px;text-align:right;color:{TEXT_MUTED};font-size:11px;font-weight:500;">浮盈</th>'
        f'<th style="padding:8px;text-align:right;color:{TEXT_MUTED};font-size:11px;font-weight:500;">持仓回撤</th>'
        f'<th style="padding:8px;text-align:right;color:{TEXT_MUTED};font-size:11px;font-weight:500;">分数</th>'
        f'</tr>'
    )
    return (
        f'<div style="background:{BG_PANEL};border:1px solid {BORDER};border-radius:8px;'
        f'overflow:hidden;margin-bottom:16px;">'
        f'<div style="padding:10px 14px;background:{BG_PANEL_HI};border-bottom:1px solid {BORDER};">'
        f'<span style="color:{TEXT};font-size:13px;font-weight:600;">💼 当前持仓 (模拟)</span>'
        f'<span style="color:{TEXT_MUTED};font-size:11px;margin-left:8px;">(回撤 &gt; 10% 触发止损)</span>'
        f'</div>'
        f'<table style="width:100%;border-collapse:collapse;font-size:12px;">{header}{"".join(rows)}</table>'
        f'</div>'
    )


def render(df_res, df_hist):
    """主渲染入口 — 由 app.py / render_all_tabs 调用

    注意: 这个 Tab 不使用 df_res/df_hist (那是 ETF 数据)
    直接从 stock_cache 加载
    """
    st.markdown(
        f'<div style="margin:8px 0 4px 0;">'
        f'<span style="color:{TEXT};font-size:18px;font-weight:600;">🎯 强弱选股策略</span>'
        f'<span style="color:{TEXT_MUTED};font-size:12px;margin-left:10px;">'
        f'基于策略说明书 v3.0 · 全市场 Top 30 等权 · 创业板指基准</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # 1. 检查数据
    data = _load_all_stocks()
    pool = data["pool"]
    panel = data["panel"]
    meta = data["meta"]
    bench = _load_benchmark()
    if not pool or panel.empty or bench.empty:
        st.error(
            f"❌ stock_cache 缺失或不可用\n\n"
            f"- stock_cache 路径: `{STOCK_CACHE_DIR}`\n"
            f"- 创业板指 CSV: `{BENCHMARK_CODE}.csv`\n"
            f"- 请确认 `/home/ubuntu/.openclaw/workspace/stock_cache/` 下有 {BENCHMARK_CODE}.csv 和 A 股 CSV"
        )
        return

    # 2. 选择截止日期
    available_dates = bench["date"].dt.strftime("%Y-%m-%d").tolist()
    default_idx = len(available_dates) - 1
    asof = st.selectbox(
        "📅 截止日期",
        options=available_dates,
        index=default_idx,
        help="选择策略计算所用的截止日期 (默认最新)",
    )

    # 3. 计算
    with st.spinner("正在计算全市场 Top 30 候选..."):
        candidates = compute_top_candidates(asof)
        signal = compute_rebalance_signal(asof, candidates)
        regime_df = compute_market_regime(bench[bench["date"] <= pd.Timestamp(asof)])

    if candidates.empty:
        st.warning("⚠️ 候选池为空 — 可能是数据日期不匹配或 MA200 过滤太严")
        return

    regime = candidates["regime"].iloc[0]
    pos_ratio = POSITION_RATIO.get(regime, 0.6)
    n_passed = int(candidates["passed_threshold"].sum())
    n_hold = signal.get("n_holdings", 0) if isinstance(signal, dict) else 0

    # 4. KPI 卡片
    st.markdown(
        _regime_card_html(regime, pos_ratio, n_passed, n_hold),
        unsafe_allow_html=True,
    )

    # 5. 大盘状态色条
    if not regime_df.empty:
        st.markdown(_regime_strip_html(regime_df), unsafe_allow_html=True)

    # 6. 调仓信号
    if isinstance(signal, dict) and "error" not in signal:
        st.markdown(_rebalance_signal_html(signal), unsafe_allow_html=True)

    # 7. 候选池 + 持仓
    st.markdown(_candidates_table_html(candidates, top_n=TOP_N), unsafe_allow_html=True)
    if isinstance(signal, dict) and "current_holdings" in signal:
        st.markdown(_holdings_table_html(signal["current_holdings"]), unsafe_allow_html=True)

    # 7.5 回测曲线 (v2)
    st.markdown(
        f'<div style="margin:18px 0 8px 0;">'
        f'<span style="color:{TEXT};font-size:15px;font-weight:600;">📈 历史回测 (v2)</span>'
        f'<span style="color:{TEXT_MUTED};font-size:11px;margin-left:10px;">'
        f'逐日 Top 30 等权 + -10% 止损 · 净值曲线 · 业绩指标</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    # 区间选择
    bt_dates = bench["date"]
    bt_min = bench["date"].iloc[0].strftime("%Y-%m-%d")
    bt_max = bench["date"].iloc[-1].strftime("%Y-%m-%d")
    one_year_ago = (bench["date"].iloc[-1] - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
    # session_state 预填 (用 one_year_ago 作为起始, 用户可以改)
    if "_bt_start" not in st.session_state:
        st.session_state["_bt_start"] = one_year_ago
    if "_bt_end" not in st.session_state:
        st.session_state["_bt_end"] = bt_max
    bt_col1, bt_col2, bt_col3 = st.columns([2, 2, 1])
    with bt_col1:
        bt_start = st.text_input("📅 回测起始", key="_bt_start",
                                  help=f"YYYY-MM-DD, 默认 {one_year_ago}")
    with bt_col2:
        bt_end = st.text_input("📅 回测结束", key="_bt_end",
                                help="YYYY-MM-DD")
    with bt_col3:
        bt_run = st.button("🚀 跑回测", key="_bt_run", type="primary")

    # 只有点按钮才跑回测 (避免首次进入 tab 自动启动 5 分钟计算)
    if bt_run:
        # 简单验证区间
        try:
            start_ts = pd.Timestamp(bt_start)
            end_ts = pd.Timestamp(bt_end)
        except Exception as e:
            st.error(f"日期格式错误: {e}")
            return
        if start_ts >= end_ts:
            st.error("起始日需要早于结束日")
            return
        with st.spinner(f"回测中: {bt_start} → {bt_end} (逐日调用 Top 30, 约需 1-5 分钟)..."):
            bt = compute_backtest_curve(bt_start, bt_end)
        st.session_state["_bt_done"] = True
        st.session_state["_bt_result"] = bt

    bt = st.session_state.get("_bt_result", {})
    if "error" in bt:
        st.error(f"回测错误: {bt['error']}")
    elif not bt:
        # 未跑过
        st.markdown(
            f'<div style="background:{BG_PANEL};border:1px solid {BORDER};border-radius:8px;'
            f'padding:24px;color:{TEXT_MUTED};text-align:center;font-size:13px;">'
            f'👇 选择起始/结束日期，点上方"🚀 跑回测"按钮查看净值曲线和业绩归因'
            f'<div style="margin-top:8px;font-size:11px;color:{TEXT_DIM};">'
            f'1 年区间约需 4-5 分钟 · 三个月区间约需 1-2 分钟</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        m = bt["metrics"]
        st.markdown(_backtest_kpi_html(m), unsafe_allow_html=True)
        st.markdown(_backtest_chart_html(bt), unsafe_allow_html=True)
        # 区间信息
        st.markdown(
            f'<div style="color:{TEXT_DIM};font-size:11px;margin:-6px 0 14px 0;">'
            f'回测区间: {bt["dates"][0]} → {bt["dates"][-1]} (共 {m["n_days"]} 个交易日) · '
            f'算法: 逐日 Top 30 等权 + -10% 止损 + 调仓当日扣 0.05% 交易成本</div>',
            unsafe_allow_html=True,
        )

        # 分年度对比
        yearly = bt.get("yearly", {})
        if yearly:
            st.markdown(
                f'<div style="margin:18px 0 8px 0;">'
                f'<span style="color:{TEXT};font-size:15px;font-weight:600;">📅 分年度业绩对比</span>'
                f'<span style="color:{TEXT_MUTED};font-size:11px;margin-left:10px;">各年度独立计算</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            st.markdown(_yearly_compare_html(yearly), unsafe_allow_html=True)

        # 业绩归因
        contrib_df = bt.get("contribution", pd.DataFrame())
        if not contrib_df.empty:
            st.markdown(
                f'<div style="margin:18px 0 8px 0;">'
                f'<span style="color:{TEXT};font-size:15px;font-weight:600;">🎯 业绩归因</span>'
                f'<span style="color:{TEXT_MUTED};font-size:11px;margin-left:10px;">'
                f'逐只累计贡献 · Top 10 vs Bottom 5</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            st.markdown(_attribution_table_html(contrib_df), unsafe_allow_html=True)

    # 8. 说明
    with st.expander("📖 策略说明 (v3.0)", expanded=False):
        st.markdown(
            f"""
**选股规则**:
- 因子打分 = `rank(20日α) × 40% + rank(5日α) × 40% + rank(10日α) × 20%`
- **MA200 硬过滤**: 收盘价必须站上 200 日均线
- **分数门槛**: ≥ 60 (阶段底期降至 50)
- **见底加成**: 弱势/横盘期, 个股提前见底 +15 分

**大盘状态** (创业板指 {BENCHMARK_CODE}):
- 强势: 收盘 > MA20 + 斜率 > 0.5% → 仓位 100%
- 阶段底: 弱势/横盘 → 强势拐点 + 未来 7 天 → 仓位 100%
- 横盘: 其他情况 → 仓位 60%
- 弱势: 收盘 < MA20 + 斜率 < -0.5% → 仓位 40%

**调仓规则**:
- 买入: 从 Top 30 候选中按分数降序, 补到 30 只为止
- 卖出: 触发最高点回撤 10% 或分数持续过低
- 当日止损 → 次日才补新 (防日内反复交易)

**排除**: 北交所 (8/4 开头) + 科创板 (688 开头)
            """
        )

    # 9. 性能 / 数据信息
    with st.expander("ℹ️ 数据信息", expanded=False):
        st.markdown(
            f"""
- **股票池**: {len(pool)} 只 (已剔除北交所+科创板)
- **K 线区间**: {meta['first_date'].min().strftime('%Y-%m-%d')} ~ {meta['last_date'].max().strftime('%Y-%m-%d')}
- **创业板指**: {len(bench)} 个交易日
- **计算 asof**: {asof}
- **当前大盘状态**: {regime} (建议仓位 {pos_ratio*100:.0f}%)
            """
        )
