"""
羊羊股市监测 — ETF 强弱趋势分析
重做版 v2:深色金融主题 + 大厂设计标准
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
import datetime

# ============================================================
# 配置
# ============================================================
DATA_DIR = Path(__file__).parent / "data"
ASOF_FILE = DATA_DIR / ".asof"

# 主题色 — 深色金融(参考东财/同花顺)
BG          = "#0a0e1a"   # 主背景
BG_PANEL    = "#131826"   # 卡片背景
BG_PANEL_HI = "#1a2030"   # 卡片 hover
BORDER      = "#1f2638"
BORDER_HI   = "#2a334a"
TEXT        = "#e8eaef"   # 主文字
TEXT_MUTED  = "#7a7f96"   # 次文字
TEXT_DIM    = "#54586b"   # 弱化文字
ACCENT_UP   = "#ff4d4f"   # 涨(中国红)
ACCENT_DN   = "#00d4aa"   # 跌(中国绿)
ACCENT_NEU  = "#7a7f96"   # 中性
ACCENT_HOT  = "#ff7800"   # 强势橙

# 趋势分类配色 — 色块化
LABEL_COLORS = {
    "超强势":  ("#ff3b5c", "#ffffff"),  # 红
    "强势":    ("#ff7800", "#ffffff"),  # 橙
    "震荡上涨":("#ffcc00", "#0a0e1a"),  # 黄
    "横盘震荡":("#3a4156", "#c5c8d6"),  # 灰
    "震荡下跌":("#4a90d9", "#ffffff"),  # 蓝
    "一直下跌":("#1f3556", "#7a8aa8"),  # 深蓝
}
LABEL_ORDER = ["超强势", "强势", "震荡上涨", "横盘震荡", "震荡下跌", "一直下跌"]

st.set_page_config(
    page_title="羊羊股市监测",
    page_icon="🐑",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"About": "羊羊股市监测 · ETF 强弱趋势分析"},
)

# ============================================================
# 全局 CSS — 深色金融主题
# ============================================================
st.markdown(f"""
<style>
  /* === 整体 === */
  .stApp {{
    background: {BG};
    color: {TEXT};
  }}
  [data-testid="stHeader"] {{ background: transparent !important; }}
  [data-testid="stToolbar"] {{ display: none; }}
  #MainMenu {{ visibility: hidden; }}
  footer {{ visibility: hidden; }}

  /* === 字体 === */
  html, body, [class*="css"] {{
    font-family: -apple-system, "SF Pro SC", "PingFang SC", "Microsoft YaHei", sans-serif;
    color: {TEXT};
  }}
  code, pre, .mono {{
    font-family: "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
    font-feature-settings: "tnum";
  }}

  /* === 侧边栏 === */
  [data-testid="stSidebar"] {{
    background: {BG_PANEL};
    border-right: 1px solid {BORDER};
  }}
  [data-testid="stSidebar"] h1,
  [data-testid="stSidebar"] h2,
  [data-testid="stSidebar"] h3 {{
    color: {TEXT};
  }}

  /* === Metric === */
  [data-testid="stMetricValue"] {{
    font-size: 28px;
    font-weight: 600;
    color: {TEXT};
    font-family: "SF Mono", monospace;
    font-feature-settings: "tnum";
  }}
  [data-testid="stMetricLabel"] {{
    color: {TEXT_MUTED};
    font-size: 12px;
    font-weight: 500;
  }}
  [data-testid="stMetricDelta"] {{
    color: {TEXT_MUTED};
  }}

  /* === Tabs === */
  .stTabs [data-baseweb="tab-list"] {{
    gap: 4px;
    background: transparent;
    border-bottom: 1px solid {BORDER};
  }}
  .stTabs [data-baseweb="tab"] {{
    height: 40px;
    background: transparent;
    color: {TEXT_MUTED};
    border-radius: 6px 6px 0 0;
    font-weight: 500;
    padding: 0 16px;
  }}
  .stTabs [aria-selected="true"] {{
    background: {BG_PANEL};
    color: {TEXT};
    border-bottom: 2px solid {ACCENT_UP};
  }}

  /* === DataFrame === */
  [data-testid="stDataFrame"] {{
    background: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
  }}

  /* === Slider === */
  .stSlider [data-baseweb="slider"] [role="slider"] {{
    background: {ACCENT_UP};
  }}

  /* === Input === */
  [data-testid="stSidebar"] input,
  [data-testid="stSidebar"] textarea {{
    background: {BG} !important;
    color: {TEXT} !important;
    border: 1px solid {BORDER} !important;
  }}
  [data-testid="stSidebar"] input:focus {{
    border-color: {ACCENT_UP} !important;
  }}

  /* === Selectbox / radio === */
  [data-testid="stSidebar"] [data-baseweb="select"] {{
    background: {BG};
    border: 1px solid {BORDER};
  }}

  /* === 隐藏多余 === */
  .block-container {{ padding-top: 2rem; padding-bottom: 2rem; }}
</style>
""", unsafe_allow_html=True)

# ============================================================
# 数据
# ============================================================
@st.cache_data(ttl=300)
def load_results() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "results.csv")

@st.cache_data(ttl=300)
def load_history() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "etf_trend_history.csv")

@st.cache_data(ttl=300)
def load_points() -> list[str]:
    return [c for c in load_history().columns if c not in ("code", "name")]

# ============================================================
# 工具
# ============================================================
def fmt_int(v) -> str:
    if pd.isna(v): return "—"
    return f"{int(v):,}"

def fmt_yi(v) -> str:
    """亿元:千分位 + 1 位小数"""
    if pd.isna(v): return "—"
    return f"{v:,.1f}"

def fmt_pct(v) -> str:
    if pd.isna(v): return "—"
    return f"{v*100:.1f}%"

def fmt_num(v, d=2) -> str:
    if pd.isna(v) or v is None: return "—"
    return f"{v:,.{d}f}"

def trend_color_class(label: str) -> str:
    m = {
        "超强势": "super", "强势": "strong", "震荡上涨": "up",
        "横盘震荡": "side", "震荡下跌": "down", "一直下跌": "fall",
    }
    return m.get(label, "side")

def label_badge_html(label: str) -> str:
    """色块化的趋势标签"""
    bg, fg = LABEL_COLORS.get(label, ("#3a4156", "#fff"))
    return (
        f'<span style="'
        f'background:{bg};color:{fg};'
        f'padding:3px 10px;border-radius:4px;'
        f'font-size:11px;font-weight:600;'
        f'letter-spacing:0.5px;display:inline-block;'
        f'white-space:nowrap;">{label}</span>'
    )

def kpi_card(title: str, value: str, sub: str = "", color: str = TEXT) -> str:
    """自渲染 KPI 卡(比 st.metric 自由)"""
    return f"""
    <div style="
        background:{BG_PANEL};
        border:1px solid {BORDER};
        border-radius:8px;
        padding:16px 18px;
        height:96px;
    ">
      <div style="color:{TEXT_MUTED};font-size:11px;font-weight:500;
                  letter-spacing:0.5px;text-transform:uppercase;">
        {title}
      </div>
      <div style="color:{color};font-size:28px;font-weight:600;
                  font-family:'SF Mono',monospace;
                  margin-top:6px;line-height:1.2;">
        {value}
      </div>
      <div style="color:{TEXT_DIM};font-size:11px;margin-top:4px;">
        {sub}
      </div>
    </div>
    """

# ============================================================
# 顶部
# ============================================================
def render_header(df: pd.DataFrame):
    asof = df["asof_date"].iloc[0] if "asof_date" in df.columns and len(df) else "—"
    if isinstance(asof, str) and len(asof) == 8 and asof.isdigit():
        asof_str = f"{asof[:4]}-{asof[4:6]}-{asof[6:]}"
    else:
        asof_str = str(asof)
    n = len(df)

    st.markdown(f"""
    <div style="display:flex;align-items:baseline;gap:16px;margin-bottom:4px;
                padding-bottom:16px;border-bottom:1px solid {BORDER};">
      <div style="display:flex;align-items:baseline;gap:10px;">
        <h1 style="margin:0;font-size:26px;font-weight:700;color:{TEXT};
                   letter-spacing:-0.5px;">
          羊羊股市监测
        </h1>
        <span style="color:{TEXT_MUTED};font-size:14px;font-weight:400;">
          A 股 ETF 强弱趋势分析
        </span>
      </div>
      <div style="margin-left:auto;display:flex;gap:24px;align-items:baseline;">
        <div>
          <span style="color:{TEXT_DIM};font-size:11px;">数据日期</span>
          <span style="color:{TEXT};font-size:13px;font-weight:600;
                       font-family:monospace;margin-left:6px;">{asof_str}</span>
        </div>
        <div>
          <span style="color:{TEXT_DIM};font-size:11px;">标的池</span>
          <span style="color:{ACCENT_UP};font-size:13px;font-weight:600;
                       font-family:monospace;margin-left:6px;">{n} 只</span>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

# ============================================================
# KPI 区
# ============================================================
def render_kpi(df: pd.DataFrame):
    """顶部 6 个 KPI + 总计"""
    total_size = df["fund_size_yi"].sum() if "fund_size_yi" in df.columns else 0
    cols = st.columns(7, gap="small")
    # 总计
    with cols[0]:
        st.markdown(kpi_card(
            "标的池 / 总规模",
            f"{len(df):,}",
            f"{fmt_yi(total_size)} 亿元",
            color=TEXT,
        ), unsafe_allow_html=True)
    # 各趋势分类
    for i, label in enumerate(LABEL_ORDER, start=1):
        sub = df[df["strength_label"] == label]
        count = len(sub)
        size = sub["fund_size_yi"].sum() if "fund_size_yi" in sub.columns else 0
        pct = count / len(df) * 100 if len(df) > 0 else 0
        bg, fg = LABEL_COLORS[label]
        with cols[i]:
            st.markdown(kpi_card(
                label,
                f"{count}",
                f"{fmt_yi(size)} 亿 · {pct:.1f}%",
                color=bg,
            ), unsafe_allow_html=True)

# ============================================================
# 侧边栏筛选
# ============================================================
def render_sidebar(df: pd.DataFrame) -> dict:
    with st.sidebar:
        st.markdown("### 🎛️ 筛选")
        cats = ["全部"] + sorted(df["category"].dropna().unique().tolist())
        cat = st.selectbox("行业分类", cats, index=0, label_visibility="collapsed")
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        labels = ["全部"] + LABEL_ORDER
        label = st.selectbox("趋势标签", labels, index=0, label_visibility="collapsed")
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        top_n = st.slider("Top N", 10, 200, 50, 10, label_visibility="collapsed")
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

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
        sort_by = st.selectbox(
            "排序字段", list(sort_options.keys()),
            format_func=lambda k: sort_options[k], index=0,
            label_visibility="collapsed",
        )
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        sort_dir_label = st.radio(
            "排序方向", ["降序 ⬇", "升序 ⬆"],
            horizontal=True, label_visibility="collapsed",
        )
        sort_dir = "desc" if "降" in sort_dir_label else "asc"

        st.markdown("---")
        search = st.text_input("🔍 搜索(代码/名称)", "", label_visibility="collapsed")
        st.caption("支持 代码 / 名称 模糊搜索")

    return {
        "category": cat, "label": label, "top_n": top_n,
        "sort_by": sort_by, "sort_dir": sort_dir, "search": search.strip().lower(),
    }

# ============================================================
# 过滤 + 排序
# ============================================================
def apply_filters(df: pd.DataFrame, f: dict) -> pd.DataFrame:
    out = df.copy()
    if f["category"] != "全部":
        out = out[out["category"] == f["category"]]
    if f["label"] != "全部":
        out = out[out["strength_label"] == f["label"]]
    if f["search"]:
        s = f["search"]
        out = out[
            out["code"].astype(str).str.contains(s, case=False, na=False) |
            out["name"].astype(str).str.contains(s, case=False, na=False)
        ]
    return out

def sort_df(df: pd.DataFrame, sort_by: str, sort_dir: str) -> pd.DataFrame:
    out = df.copy()
    ascending = (sort_dir == "asc")
    if sort_by == "strength_label":
        LABEL_W = {l: i for i, l in enumerate(reversed(LABEL_ORDER))}
        out["_w"] = out["strength_label"].map(LABEL_W).fillna(-1)
        out = out.sort_values(
            by=["_w", "slope_50"], ascending=ascending, na_position="last",
        ).drop(columns="_w")
    else:
        out = out.sort_values(sort_by, ascending=ascending, na_position="last")
    return out

# ============================================================
# 主表格
# ============================================================
def render_table(df: pd.DataFrame):
    if df.empty:
        st.info("无匹配数据")
        return

    cols_order = [
        "code", "name", "strength_label", "category",
        "slope_50", "slope_20", "slope_120",
        "sharpe_composite", "adx", "up_ratio_60",
        "fund_size_yi", "n_points",
    ]
    show = df[[c for c in cols_order if c in df.columns]].copy()

    # 重命名
    show = show.rename(columns={
        "code": "代码", "name": "名称", "strength_label": "趋势",
        "category": "分类", "slope_50": "50日斜率",
        "slope_20": "20日斜率", "slope_120": "120日斜率",
        "sharpe_composite": "夏普", "adx": "ADX",
        "up_ratio_60": "60日↑%", "fund_size_yi": "规模(亿)",
        "n_points": "样本",
    })

    # 趋势列 → HTML 标签
    show["趋势"] = show["趋势"].apply(label_badge_html)

    # 数值列:千分位 / 小数
    def color_num(v):
        if pd.isna(v): return ""
        if v > 0: return "color: #ff4d4f"
        if v < 0: return "color: #00d4aa"
        return "color: #7a7f96"

    # 渲染 — 用 Styler 让"趋势"列展示色块
    st.caption(f"显示前 {len(show)} 只 · 列名按金融习惯命名")
    st.write(
        show.to_html(escape=False, index=False, border=0, classes="etf-table"),
        unsafe_allow_html=True,
    )
    st.markdown(f"""
    <style>
      .etf-table {{
        width: 100%;
        border-collapse: collapse;
        font-family: "SF Mono", monospace;
        font-size: 13px;
        background: {BG_PANEL};
        border-radius: 8px;
        overflow: hidden;
        border: 1px solid {BORDER};
      }}
      .etf-table th {{
        background: {BG_PANEL_HI};
        color: {TEXT_MUTED};
        font-weight: 500;
        font-size: 11px;
        text-align: left;
        padding: 12px 14px;
        border-bottom: 1px solid {BORDER};
        text-transform: uppercase;
        letter-spacing: 0.5px;
        white-space: nowrap;
      }}
      .etf-table td {{
        padding: 12px 14px;
        border-bottom: 1px solid {BORDER};
        color: {TEXT};
        white-space: nowrap;
        font-feature-settings: "tnum";
      }}
      .etf-table tr:hover td {{
        background: {BG_PANEL_HI};
      }}
      .etf-table tr:last-child td {{
        border-bottom: none;
      }}
      .etf-table td:nth-child(3) {{ white-space: normal; }}
    </style>
    """, unsafe_allow_html=True)

# ============================================================
# 图表
# ============================================================
PLOTLY_THEME = go.layout.Template({
    "layout": {
        "paper_bgcolor": BG_PANEL,
        "plot_bgcolor": BG_PANEL,
        "font": {"color": TEXT, "family": "Inter, -apple-system, sans-serif", "size": 12},
        "colorway": [LABEL_COLORS[l][0] for l in LABEL_ORDER],
        "xaxis": {"gridcolor": BORDER, "zerolinecolor": BORDER, "linecolor": BORDER,
                  "tickcolor": TEXT_MUTED, "tickfont": {"color": TEXT_MUTED}},
        "yaxis": {"gridcolor": BORDER, "zerolinecolor": BORDER, "linecolor": BORDER,
                  "tickcolor": TEXT_MUTED, "tickfont": {"color": TEXT_MUTED}},
        "margin": {"t": 20, "r": 20, "b": 40, "l": 50},
    }
})

def render_pie(df: pd.DataFrame):
    counts = df["strength_label"].value_counts().reindex(LABEL_ORDER, fill_value=0).reset_index()
    counts.columns = ["趋势", "数量"]
    counts = counts[counts["数量"] > 0]

    fig = go.Figure()
    fig.add_trace(go.Pie(
        labels=counts["趋势"],
        values=counts["数量"],
        hole=0.55,
        marker=dict(colors=[LABEL_COLORS[l][0] for l in counts["趋势"]],
                    line=dict(color=BG_PANEL, width=2)),
        textinfo="label+percent",
        textposition="outside",
        textfont=dict(size=12, color=TEXT),
        hovertemplate="<b>%{label}</b><br>数量:%{value}<br>占比:%{percent}<extra></extra>",
    ))
    fig.update_layout(
        template=PLOTLY_THEME,
        showlegend=False,
        height=380,
        margin=dict(t=20, r=20, b=20, l=20),
        annotations=[dict(text=f'<b style="font-size:24px">{len(df)}</b><br>'
                            f'<span style="font-size:11px;color:{TEXT_MUTED}">只 ETF</span>',
                          x=0.5, y=0.5, font_size=24, showarrow=False)],
    )
    return fig

def render_bar(df: pd.DataFrame):
    grouped = df.groupby("category").agg(
        total=("code", "count"),
        strong=("strength_label", lambda s: s.isin(["超强势", "强势"]).sum()),
    ).reset_index()
    grouped["strong_pct"] = grouped["strong"] / grouped["total"] * 100
    grouped = grouped.sort_values("total", ascending=False).head(12)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="强势+超强势", x=grouped["category"], y=grouped["strong"],
        marker=dict(color=LABEL_COLORS["强势"][0],
                    line=dict(color=LABEL_COLORS["强势"][0], width=0)),
        text=grouped["strong"], textposition="inside", textfont=dict(color="#fff", size=11),
        hovertemplate="<b>%{x}</b><br>强势数:%{y}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="全部", x=grouped["category"], y=grouped["total"],
        marker=dict(color="rgba(79,140,255,0.35)", line=dict(width=0)),
        text=grouped["total"], textposition="inside", textfont=dict(color=TEXT, size=11),
        hovertemplate="<b>%{x}</b><br>总数:%{y}<extra></extra>",
    ))
    fig.update_layout(
        template=PLOTLY_THEME,
        barmode="overlay",
        height=380,
        xaxis_tickangle=-30,
        legend=dict(orientation="h", y=-0.25, x=0.5, xanchor="center",
                    font=dict(size=12, color=TEXT_MUTED),
                    bgcolor="rgba(0,0,0,0)"),
    )
    return fig

def render_scatter(df: pd.DataFrame):
    d = df[df["fund_size_yi"] > 0].copy()
    d["log_size"] = np.log10(d["fund_size_yi"] + 1)
    fig = go.Figure()
    for label in LABEL_ORDER:
        sub = d[d["strength_label"] == label]
        if sub.empty: continue
        bg, fg = LABEL_COLORS[label]
        fig.add_trace(go.Scatter(
            x=sub["slope_50"], y=sub["log_size"],
            mode="markers", name=label,
            marker=dict(color=bg, size=10, opacity=0.75,
                        line=dict(color=fg, width=0)),
            text=sub["name"], customdata=sub[["code", "fund_size_yi"]].values,
            hovertemplate=("<b>%{text}</b><br>"
                           "代码:%{customdata[0]}<br>"
                           "50日斜率:%{x:.2f}<br>"
                           "规模:%{customdata[1]:.1f} 亿<extra></extra>"),
        ))
    fig.update_layout(
        template=PLOTLY_THEME,
        height=420,
        xaxis=dict(title="50日斜率", gridcolor=BORDER),
        yaxis=dict(title="规模 log10(亿)",
                   tickvals=[0, 1, 2, 3], ticktext=["1亿", "10亿", "100亿", "1000亿"],
                   gridcolor=BORDER),
        legend=dict(orientation="h", y=-0.15, x=0.5, xanchor="center",
                    font=dict(size=11, color=TEXT_MUTED),
                    bgcolor="rgba(0,0,0,0)"),
        margin=dict(t=20, r=20, b=60, l=60),
    )
    return fig

# ============================================================
# 趋势演变(热力图)
# ============================================================
def render_history_heatmap(df_hist: pd.DataFrame, df_res: pd.DataFrame, f: dict):
    cat_map = dict(zip(df_res["code"], df_res["category"]))
    df = df_hist.copy()
    if f["category"] != "全部":
        df = df[df["code"].map(cat_map) == f["category"]]
    if f["label"] != "全部":
        points = load_points()
        if points:
            df = df[df[points[0]] == f["label"]]
    if f["search"]:
        s = f["search"]
        df = df[df["code"].astype(str).str.contains(s, case=False, na=False) |
                df["name"].astype(str).str.contains(s, case=False, na=False)]

    if df.empty:
        st.info("无匹配数据")
        return

    points = load_points()
    df_show = df.head(50).copy()
    # 取前几天太多(>15)改为 "X 天前" 格式防止太密
    n_show = min(15, len(points))
    points_show = list(reversed(points))[:n_show]  # 最新在左

    # 构造热力图背景 + 注释
    n_rows = len(df_show)
    n_cols = n_show

    # 颜色矩阵 — 用数字 0-5 表示趋势档位
    LABEL_NUM = {l: i for i, l in enumerate(LABEL_ORDER)}
    z = []
    text = []
    for _, row in df_show.iterrows():
        z_row = []
        t_row = []
        for p in points_show:
            v = row[p] if pd.notna(row[p]) else ""
            z_row.append(LABEL_NUM.get(v, -1))
            t_row.append(v if v else "—")
        z.append(z_row)
        text.append(t_row)

    fig = go.Figure(data=go.Heatmap(
        z=z,
        text=text,
        texttemplate="%{text}",
        textfont={"size": 10, "color": "#fff", "family": "PingFang SC"},
        colorscale=[
            [0.0,  LABEL_COLORS["一直下跌"][0]],
            [0.2,  LABEL_COLORS["震荡下跌"][0]],
            [0.4,  LABEL_COLORS["横盘震荡"][0]],
            [0.6,  LABEL_COLORS["震荡上涨"][0]],
            [0.8,  LABEL_COLORS["强势"][0]],
            [1.0,  LABEL_COLORS["超强势"][0]],
        ],
        zmin=0, zmax=5,
        showscale=False,
        xgap=2, ygap=2,
        hovertemplate="<b>%{y}</b><br>%{x}<br>趋势:%{text}<extra></extra>",
    ))

    # X 轴用日期 MM/DD
    x_disp = []
    for p in points_show:
        date_str = p.split("_")[1] if "_" in p else p
        if "-" in date_str and len(date_str) == 10:
            x_disp.append(f"{date_str[5:7]}/{date_str[8:10]}")
        else:
            x_disp.append(date_str)

    fig.update_layout(
        template=PLOTLY_THEME,
        height=max(400, n_rows * 22 + 80),
        xaxis=dict(
            side="top",
            tickmode="array", tickvals=x_disp, ticktext=x_disp,
            showgrid=False, zeroline=False,
            tickfont=dict(color=TEXT_MUTED, size=10),
            title=None,
        ),
        yaxis=dict(
            autorange="reversed",
            tickmode="array",
            tickvals=list(range(n_rows)),
            ticktext=[f'{r["code"]} {r["name"]}' for _, r in df_show.iterrows()],
            showgrid=False, zeroline=False,
            tickfont=dict(color=TEXT, size=11, family="monospace"),
        ),
        margin=dict(t=40, r=20, b=20, l=200),
        plot_bgcolor=BG_PANEL,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        f"共 {len(df_show)} 只(超 50 请缩小筛选) · "
        f"展示近 {n_show} 天 · "
        f"日期从左(最新)到右(最远) · "
        f"色块🟥超强势 🟧强势 🟨震荡上涨 ⬜横盘震荡 🟦震荡下跌 🟫一直下跌"
    )

# ============================================================
# 主流程
# ============================================================
def main():
    try:
        df_res = load_results()
        df_hist = load_history()
    except FileNotFoundError as e:
        st.error(f"❌ 数据文件缺失: {e}")
        st.info("请先运行 `python fetch_data.py` 拉取数据")
        st.stop()

    render_header(df_res)
    render_kpi(df_res)

    f = render_sidebar(df_res)

    df_view = apply_filters(df_res, f)
    df_view = sort_df(df_view, f["sort_by"], f["sort_dir"]).head(f["top_n"])

    st.markdown(f'<div style="height:16px"></div>', unsafe_allow_html=True)

    # === Tab 1: 列表 ===
    # === Tab 2: 图表 ===
    # === Tab 3: 趋势演变 ===
    tab1, tab2, tab3 = st.tabs(["📋 详细列表", "📊 图表分析", "🔥 趋势演变"])

    with tab1:
        c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
        with c1: st.metric("筛选后", f"{len(df_view):,} 只")
        with c2:
            total_s = df_view["fund_size_yi"].sum() if "fund_size_yi" in df_view.columns else 0
            st.metric("规模合计", f"{fmt_yi(total_s)} 亿")
        with c3:
            avg_sharpe = df_view["sharpe_composite"].mean() if "sharpe_composite" in df_view.columns else 0
            st.metric("平均夏普", f"{avg_sharpe:.2f}")
        with c4:
            top = df_view.iloc[0] if len(df_view) else None
            if top is not None:
                st.markdown(
                    f'<div style="text-align:right;color:{TEXT_MUTED};font-size:12px;line-height:1.5">'
                    f'排序首位:<br>'
                    f'<b style="color:{TEXT};font-size:15px;font-family:monospace">'
                    f'{top["code"]} {top["name"]}</b> · '
                    f'<span style="color:{LABEL_COLORS.get(top["strength_label"], ("#fff","#fff"))[0]}">'
                    f'{top["strength_label"]}</span></div>',
                    unsafe_allow_html=True,
                )
        st.markdown(f'<div style="height:12px"></div>', unsafe_allow_html=True)
        render_table(df_view)

    with tab2:
        c1, c2 = st.columns(2, gap="medium")
        with c1:
            st.markdown(f'<p style="color:{TEXT_MUTED};font-size:13px;margin:0 0 8px 0">'
                        f'📊 趋势分布</p>', unsafe_allow_html=True)
            st.plotly_chart(render_pie(df_res), use_container_width=True)
        with c2:
            st.markdown(f'<p style="color:{TEXT_MUTED};font-size:13px;margin:0 0 8px 0">'
                        f'📊 行业强度(Top 12)</p>', unsafe_allow_html=True)
            st.plotly_chart(render_bar(df_res), use_container_width=True)
        st.markdown(f'<div style="height:8px"></div>', unsafe_allow_html=True)
        st.markdown(f'<p style="color:{TEXT_MUTED};font-size:13px;margin:0 0 8px 0">'
                    f'📊 50日斜率 vs 规模(散点图,按趋势分组)</p>', unsafe_allow_html=True)
        st.plotly_chart(render_scatter(df_res), use_container_width=True)

    with tab3:
        st.markdown(f'<p style="color:{TEXT_MUTED};font-size:13px;margin:0 0 8px 0">'
                    f'🔥 近 {len(load_points())} 天趋势演变(色块化热力图)</p>',
                    unsafe_allow_html=True)
        render_history_heatmap(df_hist, df_res, f)

    st.markdown(f"""
    <div style="margin-top:24px;padding-top:16px;border-top:1px solid {BORDER};
                color:{TEXT_DIM};font-size:11px;text-align:center;">
      🐑 羊羊股市监测 · 数据每 5 分钟缓存 ·
      数据源:CloudBase API + AmazingData
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
