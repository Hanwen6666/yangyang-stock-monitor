"""
羊羊股市监测 — ETF 强弱趋势分析
Streamlit 版本 (单文件,可直接部署到 Streamlit Cloud)

数据源:本地 data/results.csv + data/etf_trend_history.csv
       (从 CloudBase API 抓取并归档,理财助理更新时重新跑 fetch_data.py)
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
import datetime

# ============================================================
# 配置
# ============================================================
DATA_DIR = Path(__file__).parent / "data"
ASOF_FILE = DATA_DIR / ".asof"  # 文件 mtime 就是数据生成时间

LABEL_COLORS = {
    "超强势": "#ff3b5c",
    "强势": "#ff7800",
    "震荡上涨": "#ffcc00",
    "横盘震荡": "#54586b",
    "震荡下跌": "#4a90d9",
    "一直下跌": "#2e5b8a",
}
LABEL_ORDER = ["超强势", "强势", "震荡上涨", "横盘震荡", "震荡下跌", "一直下跌"]
LABEL_WEIGHT = {l: i for i, l in enumerate(reversed(LABEL_ORDER))}  # 高分=强势

st.set_page_config(
    page_title="羊羊股市监测",
    page_icon="🐑",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# 加载数据(带缓存)
# ============================================================
@st.cache_data(ttl=300, show_spinner=False)
def load_results() -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / "results.csv")
    return df

@st.cache_data(ttl=300, show_spinner=False)
def load_history() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "etf_trend_history.csv")

@st.cache_data(ttl=300, show_spinner=False)
def load_points() -> list[str]:
    df = load_history()
    return [c for c in df.columns if c not in ("code", "name")]

# ============================================================
# 页面
# ============================================================
def render_header(df_results: pd.DataFrame):
    asof = df_results["asof_date"].iloc[0] if "asof_date" in df_results.columns and len(df_results) else "--"
    asof_str = f"{asof[:4]}-{asof[4:6]}-{asof[6:]}" if isinstance(asof, str) and len(asof) == 8 else str(asof)
    data_mtime = ASOF_FILE.stat().st_mtime if ASOF_FILE.exists() else DATA_DIR.stat().st_mtime
    last_refresh = datetime.datetime.fromtimestamp(data_mtime).strftime("%Y-%m-%d %H:%M:%S")

    st.markdown(
        f"""
        <div style="display:flex;align-items:center;gap:14px;margin-bottom:8px">
          <h1 style="margin:0">🐑 羊羊股市监测</h1>
          <span style="color:#888;font-size:14px">ETF 强弱趋势分析</span>
          <span style="margin-left:auto;color:#888;font-size:13px">
            📅 数据日期:<b>{asof_str}</b> &nbsp;|&nbsp; 🔄 归档:{last_refresh}
          </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

def render_sidebar(df_results: pd.DataFrame) -> dict:
    with st.sidebar:
        st.header("🎛️ 筛选器")
        cats = sorted(df_results["category"].dropna().unique().tolist())
        cat = st.selectbox("行业分类", ["全部"] + cats)
        labels = LABEL_ORDER
        label = st.selectbox("趋势标签", ["全部"] + labels)
        top_n = st.slider("Top N", 10, 200, 50, 10)
        sort_options = {
            "strength_label": "趋势强度",
            "slope_50": "50日斜率",
            "slope_20": "20日斜率",
            "slope_120": "120日斜率",
            "sharpe_composite": "综合夏普",
            "adx": "ADX",
            "up_ratio_60": "60日上涨占比",
            "fund_size_yi": "规模(亿)",
        }
        sort_by = st.selectbox("排序字段", list(sort_options.keys()),
                               format_func=lambda k: sort_options[k],
                               index=0)
        sort_dir = st.radio("排序方向", ["desc", "asc"], format_func=lambda x: "降序 ⬇" if x == "desc" else "升序 ⬆", horizontal=True)
        st.divider()
        search = st.text_input("🔍 搜索 ETF(代码或名称)", "")
        st.caption("💡 数据每 5 分钟缓存;刷新页面 = 重新加载")
    return {
        "category": cat, "label": label, "top_n": top_n,
        "sort_by": sort_by, "sort_dir": sort_dir, "search": search.strip().lower(),
    }

def apply_filters(df: pd.DataFrame, f: dict) -> pd.DataFrame:
    out = df.copy()
    if f["category"] != "全部":
        out = out[out["category"] == f["category"]]
    if f["label"] != "全部":
        out = out[out["strength_label"] == f["label"]]
    if f["search"]:
        s = f["search"]
        out = out[out["code"].str.contains(s, case=False, na=False) | out["name"].str.contains(s, case=False, na=False)]
    return out

def sort_df(df: pd.DataFrame, sort_by: str, sort_dir: str) -> pd.DataFrame:
    """根据趋势标签/数字字段排序。strength_label 按权重，同分用 slope_50 tiebreak。"""
    out = df.copy()
    ascending = (sort_dir == "asc")
    if sort_by == "strength_label":
        out["_w"] = out["strength_label"].map(LABEL_WEIGHT).fillna(-1)
        out = out.sort_values(
            by=["_w", "slope_50"], ascending=ascending, kind="mergesort", na_position="last"
        ).drop(columns="_w")
    else:
        out = out.sort_values(sort_by, ascending=ascending, na_position="last")
    return out

# ============================================================
# 区块
# ============================================================
def render_cards(df_all: pd.DataFrame):
    st.subheader("📊 趋势分布")
    total = len(df_all)
    total_size = df_all["fund_size_yi"].sum() if "fund_size_yi" in df_all.columns else 0
    cols = st.columns(len(LABEL_ORDER) + 1)
    cols[0].metric("总计", f"{total}", f"{total_size:.1f} 亿")
    for i, label in enumerate(LABEL_ORDER, start=1):
        sub = df_all[df_all["strength_label"] == label]
        cols[i].metric(
            label,
            f"{len(sub)}",
            f"{sub['fund_size_yi'].sum():.1f} 亿" if "fund_size_yi" in sub.columns else "",
        )

def render_table(df_view: pd.DataFrame):
    st.subheader(f"📋 ETF 列表({len(df_view)} 只)")
    cols = ["code", "name", "strength_label", "slope_50", "slope_20", "slope_120",
            "sharpe_composite", "adx", "up_ratio_60", "category", "fund_size_yi"]
    show = df_view[[c for c in cols if c in df_view.columns]].copy()
    show = show.rename(columns={
        "code": "代码", "name": "名称", "strength_label": "趋势标签",
        "slope_50": "50日斜率", "slope_20": "20日斜率", "slope_120": "120日斜率",
        "sharpe_composite": "综合夏普", "adx": "ADX", "up_ratio_60": "60日上涨占比",
        "category": "分类", "fund_size_yi": "规模(亿)",
    })
    # 给趋势标签加色
    def color_label(val):
        c = LABEL_COLORS.get(val, "#888")
        return f"background-color: {c}33; color: {c}; font-weight: 600"
    try:
        st.dataframe(
            show.style.map(color_label, subset=["趋势标签"]).format({
                "50日斜率": "{:.4f}", "20日斜率": "{:.4f}", "120日斜率": "{:.4f}",
                "综合夏普": "{:.3f}", "ADX": "{:.2f}", "60日上涨占比": "{:.1%}",
                "规模(亿)": "{:.1f}",
            }, na_rep="-"),
            use_container_width=True, height=420,
        )
    except Exception:
        # 旧版 Streamlit 不支持 .map,用 formatter 替代
        st.dataframe(show, use_container_width=True, height=420)

def render_pie(df_all: pd.DataFrame):
    counts = df_all["strength_label"].value_counts().reindex(LABEL_ORDER, fill_value=0).reset_index()
    counts.columns = ["趋势", "数量"]
    fig = px.pie(counts, values="数量", names="趋势", hole=0.45,
                 color="趋势", color_discrete_map=LABEL_COLORS)
    fig.update_traces(textposition="inside", textinfo="percent+label")
    fig.update_layout(showlegend=False, margin=dict(t=10, b=10, l=0, r=0), height=280)
    st.plotly_chart(fig, use_container_width=True)

def render_bar(df_all: pd.DataFrame):
    grouped = df_all.groupby("category").agg(
        total=("code", "count"),
        strong=("strength_label", lambda s: s.isin(["超强势", "强势"]).sum()),
    ).reset_index().sort_values("total", ascending=False).head(12)
    fig = go.Figure()
    fig.add_bar(name="强势+超强势", x=grouped["category"], y=grouped["strong"],
                marker_color="#ff9500")
    fig.add_bar(name="全部", x=grouped["category"], y=grouped["total"],
                marker_color="rgba(79,140,255,0.45)")
    fig.update_layout(barmode="overlay", margin=dict(t=10, b=80, l=0, r=0), height=320,
                      xaxis_tickangle=-30, legend_orientation="h", legend_y=-0.3)
    st.plotly_chart(fig, use_container_width=True)

def render_scatter(df_all: pd.DataFrame):
    import numpy as np
    df = df_all[df_all["fund_size_yi"] > 0].copy()
    df["log_size"] = np.log10(df["fund_size_yi"] + 1)
    fig = px.scatter(
        df, x="slope_50", y="log_size", color="strength_label",
        color_discrete_map=LABEL_COLORS, hover_name="name", hover_data=["code", "fund_size_yi"],
        labels={"slope_50": "50日斜率", "log_size": "规模 log10(亿)", "strength_label": "趋势"},
    )
    fig.update_traces(marker=dict(size=9, opacity=0.7))
    fig.update_layout(margin=dict(t=10, b=10, l=0, r=0), height=380, legend_orientation="h", legend_y=-0.15)
    st.plotly_chart(fig, use_container_width=True)

def render_history_table(df_history: pd.DataFrame, df_results: pd.DataFrame, filters: dict):
    st.subheader("🔥 ETF 趋势演变")
    cat_map = dict(zip(df_results["code"], df_results["category"]))

    # 筛选
    df = df_history.copy()
    if filters["category"] != "全部":
        df = df[df["code"].map(cat_map) == filters["category"]]
    if filters["label"] != "全部":
        points = load_points()
        last_label_col = points[0]  # points[0] 是最新一天 (d_2026-07-03)
        df = df[df[last_label_col] == filters["label"]]
    if filters["search"]:
        s = filters["search"]
        df = df[df["code"].str.contains(s, case=False, na=False) | df["name"].str.contains(s, case=False, na=False)]

    if df.empty:
        st.info("无匹配数据")
        return

    # 展示:每个 cell 用 emoji 表示趋势
    emoji = {
        "超强势": "🟥", "强势": "🟧", "震荡上涨": "🟨",
        "横盘震荡": "⬜", "震荡下跌": "🟦", "一直下跌": "🟫",
    }
    points = load_points()
    display = df[["code", "name"]].copy()
    for p in points:
        display[p] = df[p].map(lambda v: emoji.get(v, "❓"))

    # 表头改成 MM/DD
    display.columns = ["代码", "名称"] + [
        f"{p.split('_')[1][5:7]}/{p.split('_')[1][8:10]}" if "_" in p and "-" in p else p
        for p in points
    ]
    st.caption(f"日期从左到右:最新 → 最远 · 共 {len(df)} 只 ETF")
    st.dataframe(display, use_container_width=True, height=380)

# ============================================================
# 主流程
# ============================================================
def main():
    try:
        df_results = load_results()
        df_history = load_history()
    except FileNotFoundError as e:
        st.error(f"❌ 数据文件缺失: {e}")
        st.info("请先运行 `python fetch_data.py` 拉取数据,或检查 `data/` 目录")
        st.stop()

    render_header(df_results)
    f = render_sidebar(df_results)

    df_view = apply_filters(df_results, f)
    df_view = sort_df(df_view, f["sort_by"], f["sort_dir"]).head(f["top_n"])

    # 顶部 cards
    render_cards(df_results)

    # 主表格 + 图表区
    tab1, tab2, tab3 = st.tabs(["📋 列表", "📈 图表", "🔥 趋势演变"])
    with tab1:
        render_table(df_view)
    with tab2:
        c1, c2 = st.columns(2)
        with c1:
            st.caption("趋势分布饼图")
            render_pie(df_results)
        with c2:
            st.caption("分类强度柱图")
            render_bar(df_results)
        st.caption("斜率 vs 规模 散点图")
        render_scatter(df_results)
    with tab3:
        render_history_table(df_history, df_results, f)

    st.divider()
    st.caption(
        "🐑 羊羊股市监测 · 数据来源:CloudBase · 自动刷新:每 5 分钟缓存 · "
        f"代码仓库:[github.com/你的用户名/羊羊股市监测]"
    )

if __name__ == "__main__":
    main()