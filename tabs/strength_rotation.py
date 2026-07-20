"""
🎯 强势股轮动 v3.0 Tab

三个子视图(顶部 segmented_control 切换):
  1. 调仓清单 — 今日 Top 30 / 调出 / 调入候选 / 持仓状态
  2. 虚拟回测 — 7 年回测曲线 / 调仓记录 / 关键统计
  3. 因子分析 — 当前候选三因子分布 / 见底加成股票
"""
import streamlit as st
import pandas as pd
import numpy as np
import sys
from pathlib import Path
from datetime import datetime

# 把项目根加进 path
sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.constants import (
    BG, BG_PANEL, BG_PANEL_HI, BORDER, BORDER_HI,
    TEXT, TEXT_MUTED, TEXT_DIM, ACCENT_UP, ACCENT_DN,
    CACHE_TTL_STATIC,  # 2026-07-20 重构: TTL 统一
)
from lib.strategy_v3 import (
    fetch_market_klines, get_a_stock_pool, run_backtest,
    generate_daily_orders, calc_market_state,
    POOL_TOP_N, BENCHMARK_CODE, POSITION_TABLE,
)
from lib.ui_components import kpi_card, metric_row_html
from html import escape


# ============================================================
# 数据加载(单例 + spinner)
# ============================================================
@st.cache_data(ttl=CACHE_TTL_STATIC, show_spinner=False)
def _load_data():
    """加载 K 线 + 大盘(快 30-60s,1 小时缓存)"""
    return fetch_market_klines(force=False)


def _load_pool_and_names():
    pool = get_a_stock_pool()
    name_map = dict(zip(pool["code"], pool["name"]))
    return pool, name_map


# ============================================================
# 子视图 1: 调仓清单
# ============================================================
def render_daily_orders(df_res, df_hist):
    pool, name_map = _load_pool_and_names()

    with st.spinner("加载全市场 K 线 + 算今日 Top 30 (首次 30-60s)..."):
        klines, benchmark = _load_data()

    orders = generate_daily_orders(klines, benchmark, name_map)
    if not orders.get("ok"):
        st.error(f"调仓清单生成失败: {orders.get('error')}")
        return

    # 顶部 KPI
    st.markdown(f'<div style="height:8px"></div>', unsafe_allow_html=True)

    asof = orders["asof_date"]
    state = orders["market_state"]
    pos_pct = orders["position_pct"]
    n_total = orders["n_total"]
    n_cand = orders["n_candidates"]
    n_in_top = len(orders["hold_list"])

    state_color = {
        "强势": ACCENT_UP, "阶段底": "#f59e0b",
        "横盘": "#7a7f96", "弱势": ACCENT_DN,
    }.get(state, TEXT_MUTED)

    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        st.markdown(kpi_card(
            title="📅 交易日期", value=asof, sub=f"数据源 399006",
            color=ACCENT_UP,
        ), unsafe_allow_html=True)
    with k2:
        st.markdown(kpi_card(
            title="🌡 大盘状态", value=state,
            sub=f"MA20 {orders['ma20']:.0f} 斜率 {orders['ma20_slope_pct']:+.2f}%",
            color=state_color,
        ), unsafe_allow_html=True)
    with k3:
        st.markdown(kpi_card(
            title="💰 仓位", value=f"{pos_pct*100:.0f}%",
            sub="等权 Top 30 × 仓位",
            color=ACCENT_UP if pos_pct == 1.0 else "#f59e0b",
        ), unsafe_allow_html=True)
    with k4:
        st.markdown(kpi_card(
            title="🎯 Top 30", value=str(n_in_top),
            sub=f"候选池 {n_cand}/{n_total}",
            color=ACCENT_UP,
        ), unsafe_allow_html=True)
    with k5:
        # [FIX] Bug #4 - candidate semantics clearer
        # original summary "调出 X 只, 调入 Y 只候选..." reads like real rebalance
        n_cand_total = len(orders['sell_candidates']) + len(orders['add_candidates'])
        st.markdown(kpi_card(
            title="📋 调仓候选",
            value=f"{n_cand_total} 只",
            sub=f"调出 {len(orders['sell_candidates'])} / 调入 {len(orders['add_candidates'])}",
            color="#f59e0b" if n_cand_total > 0 else TEXT_MUTED,
        ), unsafe_allow_html=True)

    st.markdown(f'<div style="height:12px"></div>', unsafe_allow_html=True)

    # === 调出 + 调入候选 ===
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"""
        <div style="background:{BG_PANEL};border:1px solid {BORDER};
                    border-radius:8px;padding:12px 14px;margin-bottom:8px;">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
            <span style="color:{ACCENT_DN};font-size:14px;font-weight:700;">📤 调出候选</span>
            <span style="color:{TEXT_DIM};font-size:11px;">Top 30 中分数接近门槛的尾位</span>
          </div>
        </div>
        """, unsafe_allow_html=True)
        if orders["sell_candidates"]:
            _render_orders_table(orders["sell_candidates"], mode="sell")
        else:
            st.markdown(f'<div style="color:{TEXT_DIM};font-size:12px;padding:12px;">无调出候选</div>', unsafe_allow_html=True)

    with col2:
        st.markdown(f"""
        <div style="background:{BG_PANEL};border:1px solid {BORDER};
                    border-radius:8px;padding:12px 14px;margin-bottom:8px;">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
            <span style="color:{ACCENT_UP};font-size:14px;font-weight:700;">📥 调入候选</span>
            <span style="color:{TEXT_DIM};font-size:11px;">Top 30 之外最高分(实操时按分数补入)</span>
          </div>
        </div>
        """, unsafe_allow_html=True)
        if orders["add_candidates"]:
            _render_orders_table(orders["add_candidates"], mode="add")
        else:
            st.markdown(f'<div style="color:{TEXT_DIM};font-size:12px;padding:12px;">无调入候选(已是 Top 30 之外无更优)</div>', unsafe_allow_html=True)

    st.markdown(f'<div style="height:8px"></div>', unsafe_allow_html=True)

    # === Top 30 完整持仓 ===
    st.markdown(f"""
    <div style="background:{BG_PANEL};border:1px solid {BORDER};
                border-radius:8px;padding:12px 14px;margin-bottom:8px;">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
        <span style="color:{TEXT};font-size:14px;font-weight:700;">📊 今日 Top 30 (等权持有)</span>
        <span style="color:{TEXT_DIM};font-size:11px;">按 score 降序,分数 ≥ {60 if state != '阶段底' else 50}</span>
      </div>
    </div>
    """, unsafe_allow_html=True)
    _render_orders_table(orders["hold_list"], mode="hold")


def _render_orders_table(records, mode: str = "hold"):
    """渲染调仓表 — Streamlit dataframe (sortable)"""
    if not records:
        st.markdown(f'<div style="color:{TEXT_DIM};font-size:12px;">无数据</div>', unsafe_allow_html=True)
        return
    df = pd.DataFrame(records)
    # 标准化列
    if "score" in df.columns:
        df["score"] = df["score"].round(1)
    if "price" in df.columns:
        df["price"] = df["price"].round(3)
    if "alpha_5" in df.columns:
        df["alpha_5"] = df["alpha_5"].round(2)
    if "alpha_10" in df.columns:
        df["alpha_10"] = df["alpha_10"].round(2)
    if "alpha_20" in df.columns:
        df["alpha_20"] = df["alpha_20"].round(2)
    if mode == "add":
        df = df[["code", "name", "price", "score", "alpha_5", "reason"]]
        df.columns = ["代码", "名称", "现价", "分数", "α5", "理由"]
    elif mode == "sell":
        df = df[["code", "name", "price", "score", "reason"]]
        df.columns = ["代码", "名称", "现价", "分数", "理由"]
    else:
        df = df[["code", "name", "price", "score", "alpha_5", "alpha_10", "alpha_20"]]
        df.columns = ["代码", "名称", "现价", "分数", "α5(%)", "α10(%)", "α20(%)"]
    st.dataframe(df, use_container_width=True, hide_index=True, height=min(420, 40 + 24 * len(df)))


# ============================================================
# 子视图 2: 虚拟回测
# ============================================================
def render_backtest(df_res, df_hist):
    pool, name_map = _load_pool_and_names()

    # 区间选择
    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        years = st.selectbox("回测区间", ["1年", "2年", "3年", "5年", "全部"], index=1)
    with col2:
        max_stocks = st.selectbox("标的池", ["500 (K线最长)", "1000", "2000", "全部(4500+)"], index=0)
    with col3:
        st.markdown(f'<div style="height:30px"></div>', unsafe_allow_html=True)
        run_btn = st.button("🚀 跑回测", type="primary", use_container_width=False)

    state_key = f"bt_{years}_{max_stocks}"
    if state_key not in st.session_state:
        st.session_state[state_key] = None
    if run_btn or st.session_state[state_key] is None:
        st.session_state[state_key] = "running"

    if st.session_state[state_key] == "running" or st.session_state[state_key] is None:
        with st.spinner(f"加载 K 线 + 跑 {years} 回测 (30-90s)..."):
            klines, benchmark = _load_data()

            # 取 K 线最长的子集
            n = {"500 (K线最长)": 500, "1000": 1000, "2000": 2000, "全部(4500+)": 99999}[max_stocks]
            ranked = sorted(klines.items(), key=lambda kv: len(kv[1]), reverse=True)
            sub_klines = dict(ranked[:n]) if n < len(klines) else klines

            # 区间
            bench_dates = benchmark["date"].astype(str).values
            if years == "全部":
                start_date = bench_dates[250]
            else:
                n_years = int(years.replace("年", ""))
                start_date = bench_dates[-min(250 * n_years, len(bench_dates) - 1)]
            end_date = bench_dates[-1]

            progress = st.progress(0.0, text="初始化...")
            def cb(done, total, msg, *args):
                pct = done / total if total else 0
                progress.progress(min(pct, 1.0), text=msg)
            result = run_backtest(sub_klines, benchmark, name_map,
                                   start_date=start_date, end_date=end_date,
                                   progress_cb=cb)
            progress.progress(1.0, text="完成")
            st.session_state[state_key] = result

    result = st.session_state[state_key]
    if result is None or result == "running":
        return

    stats = result.final_stats
    eq = result.equity_curve
    trades = result.trades

    # === 顶部 KPI ===
    st.markdown(f'<div style="height:8px"></div>', unsafe_allow_html=True)
    final_eq = stats["final_equity"]
    cagr = stats["cagr_pct"]
    mdd = stats["max_drawdown_pct"]
    alpha = stats["alpha_vs_bench_pct"]
    bench_ret = stats["bench_cumret_pct"]
    n_trades = stats["n_trades"]
    n_years_actual = stats["n_years"]

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        cagr_color = ACCENT_UP if cagr > 0 else ACCENT_DN
        st.markdown(kpi_card(
            title="📈 累计收益",
            value=f"{final_eq - 1:+.1%}" if abs(final_eq - 1) < 10 else f"{final_eq:.1f}x",
            sub=f"{n_years_actual} 年 ({start_date} → {end_date})",
            color=cagr_color,
        ), unsafe_allow_html=True)
    with k2:
        st.markdown(kpi_card(
            title="🚀 年化收益", value=f"{cagr:+.1f}%",
            sub=f"基准 {bench_ret:+.1f}%",
            color=ACCENT_UP if cagr > 0 else ACCENT_DN,
        ), unsafe_allow_html=True)
    with k3:
        st.markdown(kpi_card(
            title="📉 最大回撤", value=f"{mdd:.1f}%",
            sub=f"基准回撤 (略)",
            color="#f59e0b",
        ), unsafe_allow_html=True)
    with k4:
        st.markdown(kpi_card(
            title="💎 α 超额", value=f"{alpha:+.1f}%",
            sub=f"{n_trades} 次调仓",
            color=ACCENT_UP if alpha > 0 else ACCENT_DN,
        ), unsafe_allow_html=True)

    st.markdown(f'<div style="height:8px"></div>', unsafe_allow_html=True)

    # === 净值曲线 ===
    if not eq.empty:
        _render_equity_chart(eq, benchmark, start_date, end_date)

    # === 调仓记录 ===
    st.markdown(f"""
    <div style="background:{BG_PANEL};border:1px solid {BORDER};
                border-radius:8px;padding:12px 14px;margin-bottom:8px;margin-top:8px;">
      <div style="display:flex;align-items:center;gap:8px;">
        <span style="color:{TEXT};font-size:14px;font-weight:700;">📋 调仓记录</span>
        <span style="color:{TEXT_DIM};font-size:11px;">共 {len(trades)} 条 · BUY {stats['n_buy']} / SELL {stats['n_sell']}</span>
      </div>
    </div>
    """, unsafe_allow_html=True)
    if not trades.empty:
        st.dataframe(
            trades.tail(100)[["date", "action", "code", "name", "price", "pnl_pct", "reason"]]
            .sort_values("date", ascending=False)
            .rename(columns={"date": "日期", "action": "动作", "code": "代码",
                              "name": "名称", "price": "价格", "pnl_pct": "盈亏%",
                              "reason": "原因"}),
            use_container_width=True,
            hide_index=True,
            height=320,
        )


def _render_equity_chart(eq, benchmark, start_date, end_date):
    """净值曲线 — ECharts 渲染(对比创业板指)"""
    eq = eq.copy()
    eq["date"] = eq["date"].astype(str)

    # 归一化创业板指到 1.0 起
    bench_sub = benchmark[
        (benchmark["date"] >= start_date) & (benchmark["date"] <= end_date)
    ].copy()
    if not bench_sub.empty:
        bench_sub = bench_sub.reset_index(drop=True)
        bench_first = float(bench_sub["close"].iloc[0])
        bench_sub["equity"] = bench_sub["close"] / bench_first

    # 拼接到 eq 里(按 date 对齐)
    bench_aligned = bench_sub[["date", "equity"]].rename(columns={"equity": "benchmark"})
    merged = eq[["date", "equity"]].merge(bench_aligned, on="date", how="outer").sort_values("date")
    merged = merged.fillna(method="ffill").dropna()

    # 取最近 60 个点画图(防止太长)
    if len(merged) > 200:
        merged = merged.tail(200)

    # 状态着色背景
    if not eq.empty:
        states = []
        for _, r in eq.iterrows():
            color = {
                "强势": "#ff4d4f22", "阶段底": "#f59e0b22",
                "横盘": "#7a7f9622", "弱势": "#22c55e22",
            }.get(r["market_state"], "#7a7f9611")
            states.append({"date": r["date"], "state": r["market_state"], "color": color})

    dates_js = "[" + ",".join(f'"{d}"' for d in merged["date"]) + "]"
    eq_js = "[" + ",".join(f"{v:.4f}" for v in merged["equity"]) + "]"
    bench_js = "[" + ",".join(f"{v:.4f}" for v in merged["benchmark"]) + "]"

    html = f"""
    <div id="eq-chart" style="width:100%;height:380px;background:{BG_PANEL};
                               border:1px solid {BORDER};border-radius:8px;"></div>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <script>
    var chart = echarts.init(document.getElementById('eq-chart'));
    var dates = {dates_js};
    var eq = {eq_js};
    var bench = {bench_js};
    chart.setOption({{
      backgroundColor: 'transparent',
      grid: {{ left: 60, right: 60, top: 30, bottom: 40 }},
      tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'cross' }},
        backgroundColor: '#1a2030', borderColor: '#2a334a', textStyle: {{ color: '#e8eaef' }},
        formatter: function(p) {{
          var i = p[0].dataIndex;
          return p[0].name + '<br/>' +
                 '<span style="color:#ff4d4f">● 策略</span> ' + eq[i].toFixed(3) + '<br/>' +
                 '<span style="color:#60a5fa">● 创业板</span> ' + bench[i].toFixed(3);
        }}
      }},
      xAxis: {{ type: 'category', data: dates, axisLine: {{ lineStyle: {{ color: '#2a334a' }} }},
                axisLabel: {{ color: '#7a7f96', fontSize: 10 }} }},
      yAxis: {{ type: 'value', scale: true, axisLine: {{ lineStyle: {{ color: '#2a334a' }} }},
                axisLabel: {{ color: '#7a7f96', fontSize: 10, formatter: '{{value}}x' }}, splitLine: {{ lineStyle: {{ color: '#1f2638' }} }} }},
      series: [
        {{ name: '策略', type: 'line', data: eq, smooth: true, symbol: 'none',
           lineStyle: {{ color: '#ff4d4f', width: 2 }}, areaStyle: {{ color: 'rgba(255,77,79,0.08)' }} }},
        {{ name: '创业板指', type: 'line', data: bench, smooth: true, symbol: 'none',
           lineStyle: {{ color: '#60a5fa', width: 1.5, type: 'dashed' }} }}
      ]
    }});
    window.addEventListener('resize', function() {{ chart.resize(); }});
    </script>
    """
    import streamlit.components.v1 as components
    components.html(html, height=400)


# ============================================================
# 子视图 3: 因子分析
# ============================================================
def render_factor_analysis(df_res, df_hist):
    pool, name_map = _load_pool_and_names()

    with st.spinner("加载 K 线 + 算截面打分..."):
        klines, benchmark = _load_data()
        orders = generate_daily_orders(klines, benchmark, name_map)

    if not orders.get("ok"):
        st.error(f"数据加载失败: {orders.get('error')}")
        return

    st.markdown(f'<div style="height:8px"></div>', unsafe_allow_html=True)

    # 复算完整打分(为了展示见底加成)
    bench_closes = benchmark["close"].astype(float).values
    asof = orders["asof_date"]
    asof_idx = list(benchmark["date"].astype(str).values).index(asof) if asof in benchmark["date"].astype(str).values else len(benchmark) - 1
    bench_so_far = bench_closes[: asof_idx + 1]

    rows = []
    for code, kl in klines.items():
        if kl is None or len(kl) < 200:
            continue
        d = kl["date"].astype(str).values
        if asof not in d:
            continue
        pos = list(d).index(asof)
        if pos < 200:
            continue
        closes = kl["close"].astype(float).values[: pos + 1]
        from lib.strategy_v3 import calc_score, _is_early_bottom, _ma_value, MA_FILTER
        a5, a10, a20 = calc_score(closes, bench_so_far)
        ma200 = _ma_value(closes, MA_FILTER)
        rows.append({
            "code": code, "name": name_map.get(code, code),
            "alpha_5": a5, "alpha_10": a10, "alpha_20": a20,
            "close": float(closes[-1]), "ma200": ma200,
            "early_bottom": _is_early_bottom(closes),
        })

    df = pd.DataFrame(rows)

    # 三因子分布
    st.markdown(f"""
    <div style="background:{BG_PANEL};border:1px solid {BORDER};
                border-radius:8px;padding:12px 14px;margin-bottom:8px;">
      <div style="color:{TEXT};font-size:14px;font-weight:700;margin-bottom:6px;">
        📐 三因子分布 (α5 / α10 / α20)
      </div>
      <div style="color:{TEXT_DIM};font-size:11px;">
        截面百分位 rank(0-100),权重 α5×40 + α10×20 + α20×40
      </div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    for col, key, title in zip([col1, col2, col3],
                                 ["alpha_5", "alpha_10", "alpha_20"],
                                 ["α5 (短期爆发)", "α10 (中期确认)", "α20 (长期趋势)"]):
        with col:
            series = df[key].dropna()
            chart_html = _render_histogram(series, title, key)
            import streamlit.components.v1 as components
            components.html(chart_html, height=240)

    st.markdown(f'<div style="height:8px"></div>', unsafe_allow_html=True)

    # 见底加成股
    st.markdown(f"""
    <div style="background:{BG_PANEL};border:1px solid {BORDER};
                border-radius:8px;padding:12px 14px;margin-bottom:8px;">
      <div style="display:flex;align-items:center;gap:8px;">
        <span style="color:#f59e0b;font-size:14px;font-weight:700;">⭐ 个股提前见底 (大盘弱势/横盘时 +15 分加成)</span>
        <span style="color:{TEXT_DIM};font-size:11px;">振幅收窄 / 现价 > 60日低×1.05 / α10 改善</span>
      </div>
    </div>
    """, unsafe_allow_html=True)
    df_eb = df[df["early_bottom"]].copy()
    df_eb["code"] = df_eb["code"].astype(str)
    if not df_eb.empty:
        st.dataframe(
            df_eb[["code", "name", "close", "ma200", "alpha_5", "alpha_10", "alpha_20"]]
            .head(50)
            .rename(columns={"code": "代码", "name": "名称", "close": "现价",
                              "ma200": "MA200", "alpha_5": "α5", "alpha_10": "α10",
                              "alpha_20": "α20"}),
            use_container_width=True, hide_index=True, height=400,
        )
    else:
        st.info("当前无个股触发提前见底信号")


def _render_histogram(series, title, key):
    """简易 ECharts 直方图"""
    if series.empty:
        return "<div style='color:#7a7f96;padding:40px;text-align:center;'>无数据</div>"
    bins = np.linspace(series.min(), series.max(), 21)
    counts, edges = np.histogram(series, bins=bins)
    cats = [f"{edges[i]:.1f}~{edges[i+1]:.1f}" for i in range(len(counts))]
    color = "#ff4d4f" if "5" in key else ("#60a5fa" if "10" in key else "#a78bfa")
    return f"""
    <div style="background:{BG_PANEL};border:1px solid {BORDER};border-radius:8px;
                padding:10px 12px;">
      <div style="color:{TEXT_MUTED};font-size:10px;text-transform:uppercase;
                  letter-spacing:0.5px;margin-bottom:4px;">{title}</div>
      <div id="h-{key}" style="width:100%;height:160px;"></div>
      <div style="color:{TEXT_DIM};font-size:10px;margin-top:4px;">
        N={len(series)} · μ={series.mean():.2f} · σ={series.std():.2f}
      </div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <script>
    var c = echarts.init(document.getElementById('h-{key}'));
    c.setOption({{
      backgroundColor: 'transparent',
      grid: {{ left: 30, right: 10, top: 10, bottom: 40 }},
      xAxis: {{ type: 'category', data: {str(cats).replace("'", '"')},
                axisLabel: {{ color: '#7a7f96', fontSize: 8, rotate: 60 }},
                axisLine: {{ lineStyle: {{ color: '#2a334a' }} }} }},
      yAxis: {{ type: 'value', axisLabel: {{ color: '#7a7f96', fontSize: 9 }},
                axisLine: {{ lineStyle: {{ color: '#2a334a' }} }},
                splitLine: {{ lineStyle: {{ color: '#1f2638' }} }} }},
      series: [{{ type: 'bar', data: {list(counts)},
                  itemStyle: {{ color: '{color}' }} }}]
    }});
    </script>
    """


# ============================================================
# 子视图路由
# ============================================================
def render(df_res, df_hist):
    """主 render 入口 — 顶部 segmented_control 切子视图"""
    st.markdown(f"""
    <div style="background:{BG_PANEL};border:1px solid {BORDER};
                border-radius:8px;padding:12px 14px;margin-bottom:4px;">
      <div style="display:flex;align-items:center;gap:8px;">
        <span style="color:{TEXT};font-size:15px;font-weight:700;">🎯 强势股轮动 v3.0</span>
        <span style="color:{TEXT_DIM};font-size:11px;">· 全 A 强势股监测 · 30 只等权 · 创业板指择时</span>
      </div>
      <div style="color:{TEXT_DIM};font-size:10px;margin-top:4px;">
        策略说明书 v3.0 · 因子 α5/α10/α20 + MA200 过滤 + 阶段底加成 + 10% 止损
      </div>
    </div>
    """, unsafe_allow_html=True)

    view = st.segmented_control(
        "视图",
        options=["📋 调仓清单", "📊 虚拟回测", "📐 因子分析"],
        default="📋 调仓清单",
        label_visibility="collapsed",
    )

    if view == "📋 调仓清单":
        render_daily_orders(df_res, df_hist)
    elif view == "📊 虚拟回测":
        render_backtest(df_res, df_hist)
    elif view == "📐 因子分析":
        render_factor_analysis(df_res, df_hist)
