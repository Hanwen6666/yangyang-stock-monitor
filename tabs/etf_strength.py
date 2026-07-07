"""
ETF 强弱趋势分析 Tab

包含两个子视图:
  - 详细列表(全部 197 只 ETF 排序展示)
  - 趋势演变(25 天色块化热力图)

后续如果要把"详细列表"和"趋势演变"拆成两个顶层 Tab,改这里即可。
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from pathlib import Path

# 复用 app.py 的主题/工具
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


# 主题色(与 app.py 同步)
BG = "#0a0e1a"
BG_PANEL = "#131826"
BG_PANEL_HI = "#1a2030"
BORDER = "#1f2638"
BORDER_HI = "#2a334a"
TEXT = "#e8eaef"
TEXT_MUTED = "#7a7f96"
TEXT_DIM = "#54586b"
ACCENT_UP = "#ff4d4f"
LABEL_COLORS = {
    "超强势":   ("#ff3b5c", "#ffffff"),
    "强势":     ("#ff7800", "#ffffff"),
    "震荡上涨": ("#ffcc00", "#0a0e1a"),
    "横盘震荡": ("#3a4156", "#c5c8d6"),
    "震荡下跌": ("#4a90d9", "#ffffff"),
    "一直下跌": ("#1f3556", "#7a8aa8"),
}
LABEL_ORDER = ["超强势", "强势", "震荡上涨", "横盘震荡", "震荡下跌", "一直下跌"]


def label_badge_html(label: str) -> str:
    bg, fg = LABEL_COLORS.get(label, ("#3a4156", "#fff"))
    return (
        f'<span style="'
        f'background:{bg};color:{fg};'
        f'padding:3px 10px;border-radius:4px;'
        f'font-size:11px;font-weight:600;'
        f'letter-spacing:0.5px;display:inline-block;'
        f'white-space:nowrap;">{label}</span>'
    )


def sort_df(df: pd.DataFrame, sort_by: str = "strength_label", sort_dir: str = "desc") -> pd.DataFrame:
    out = df.copy()
    ascending = (sort_dir == "asc")
    if sort_by == "strength_label":
        LABEL_W = {l: i for i, l in enumerate(reversed(LABEL_ORDER))}
        out["_w"] = out["strength_label"].map(LABEL_W).fillna(-1)
        out = out.sort_values(by=["_w", "slope_50"], ascending=ascending, na_position="last").drop(columns="_w")
    else:
        out = out.sort_values(sort_by, ascending=ascending, na_position="last")
    return out


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
    show = show.rename(columns={
        "code": "代码", "name": "名称", "strength_label": "趋势",
        "category": "分类", "slope_50": "50日斜率",
        "slope_20": "20日斜率", "slope_120": "120日斜率",
        "sharpe_composite": "夏普", "adx": "ADX",
        "up_ratio_60": "60日↑%", "fund_size_yi": "规模(亿)",
        "n_points": "样本",
    })
    show["趋势"] = show["趋势"].apply(label_badge_html)

    # 数字格式化:千分位 + 智能精度
    def fmt_int(v):
        if pd.isna(v): return "—"
        return f"{int(v):,}"

    def fmt_num(v, d=2):
        if pd.isna(v): return "—"
        return f"{v:,.{d}f}"

    def fmt_yi(v):
        if pd.isna(v): return "—"
        return f"{v:,.1f}"

    def fmt_pct_val(v):
        if pd.isna(v): return "—"
        return f"{v*100:.1f}%"

    if "代码" in show.columns:
        show["代码"] = show["代码"].apply(lambda v: f"{int(v)}" if pd.notna(v) else "—")
    for col, prec in [("50日斜率", 2), ("20日斜率", 2), ("120日斜率", 2),
                       ("夏普", 3), ("ADX", 2), ("样本", 0)]:
        if col in show.columns:
            show[col] = show[col].apply(lambda v, p=prec: fmt_num(v, p))
    if "60日↑%" in show.columns:
        show["60日↑%"] = show["60日↑%"].apply(fmt_pct_val)
    if "规模(亿)" in show.columns:
        show["规模(亿)"] = show["规模(亿)"].apply(fmt_yi)

    st.markdown(
        f'<div style="color:{TEXT_DIM};font-size:10px;margin-bottom:4px;">'
        f'共 {len(show)} 只 · 按趋势强度排序</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="etf-table-wrap">'
        f'{show.to_html(escape=False, index=False, border=0, classes="etf-table")}'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown(f"""
    <style>
      .etf-table-wrap {{
        max-height: 560px; overflow-y: auto; overflow-x: auto;
        border-radius: 6px; border: 1px solid {BORDER}; background: {BG_PANEL};
      }}
      .etf-table-wrap::-webkit-scrollbar {{ width: 4px; height: 4px; }}
      .etf-table-wrap::-webkit-scrollbar-track {{ background: {BG_PANEL}; }}
      .etf-table-wrap::-webkit-scrollbar-thumb {{ background: {BORDER_HI}; border-radius: 2px; }}
      .etf-table-wrap::-webkit-scrollbar-thumb:hover {{ background: {TEXT_DIM}; }}
      .etf-table {{
        width: 100%; border-collapse: collapse;
        font-family: "SF Mono", monospace; font-size: 11px; background: {BG_PANEL};
      }}
      .etf-table th {{
        background: {BG_PANEL_HI}; color: {TEXT_DIM};
        font-weight: 500; font-size: 9px;
        text-align: left; padding: 6px 10px;
        border-bottom: 1px solid {BORDER}; text-transform: uppercase;
        letter-spacing: 0.8px; white-space: nowrap;
        position: sticky; top: 0; z-index: 1;
      }}
      .etf-table td {{
        padding: 6px 10px; border-bottom: 1px solid #151b2a;
        color: {TEXT}; white-space: nowrap; font-feature-settings: "tnum";
        font-size: 11px;
      }}
      .etf-table tr:hover td {{ background: {BG_PANEL_HI}; }}
      .etf-table tr:last-child td {{ border-bottom: none; }}
      .etf-table td:nth-child(3) {{ white-space: normal; }}
    </style>
    """, unsafe_allow_html=True)


def render_history_table(df_hist: pd.DataFrame, df_res: pd.DataFrame):
    """趋势演变子视图 — 紧凑色块矩阵
    列: 代码 / 名称 / [日期色块] (最新→最远)
    """
    if df_hist.empty or df_res.empty:
        st.info("暂无趋势历史数据")
        return

    points = [c for c in df_hist.columns if c not in ("code", "name")]
    if not points:
        st.info("无趋势数据点")
        return

    df = df_hist.copy()

    # === 筛选区: 只留日期选择 + 强弱势 ===
    fc1, fc2 = st.columns([1, 1])
    with fc1:
        # 提取所有日期作为选项
        date_options = [p.split("_")[1][:10] if "_" in p else p for p in points]
        date_options_short = {}
        for p, d in zip(points, date_options):
            if len(d) >= 10:
                date_options_short[p] = d[5:10]  # "07-03"
            else:
                date_options_short[p] = d
        # 日期筛选:多选,默认选最近20天
        date_options_rev = list(reversed(points))
        default_dates = date_options_rev[:min(20, len(date_options_rev))]
        selected_dates = st.multiselect(
            "日期", date_options_rev,
            default=default_dates,
            format_func=lambda p: date_options_short.get(p, p),
            placeholder="日期范围",
            label_visibility="collapsed",
        )
    with fc2:
        # 最新日期的趋势筛选
        latest_point = points[-1]
        trend_opts = sorted(df[latest_point].dropna().unique().tolist()) if latest_point in df.columns else []
        # 保持 LABEL_ORDER 顺序
        trend_opts = [t for t in LABEL_ORDER if t in trend_opts]
        label_filter = st.multiselect(
            "趋势", trend_opts,
            default=[],
            placeholder="趋势筛选",
            label_visibility="collapsed",
        )

    # 应用筛选
    if selected_dates:
        selected_points = selected_dates
    else:
        selected_points = points
    if label_filter:
        df = df[df[latest_point].isin(label_filter)]

    if df.empty:
        st.info("无匹配 ETF")
        return

    # === 构建展示表 ===
    points_disp = list(reversed(selected_points))  # 最新在左
    # 日期格式: "2026-07-03" → "07-03"
    col_rename = {}
    for p in points_disp:
        if "_" in p:
            raw = p.split("_")[1][:10]
            if len(raw) >= 10:
                col_rename[p] = raw[5:10]
            else:
                col_rename[p] = raw
        else:
            col_rename[p] = p

    show = df[["code", "name"] + [p for p in selected_points if p in df.columns]].copy()
    show = show.rename(columns={"code": "代码", "name": "名称"})
    show = show.rename(columns=col_rename)
    date_cols = [c for c in col_rename.values() if c in show.columns]
    # 每个日期列 → 紧凑色块
    for p, disp in col_rename.items():
        if disp in show.columns:
            show[disp] = show[disp].apply(_compact_cell_html)

    # 列顺序: 代码 + 名称 + 日期
    base_cols = ["代码", "名称"]
    show = show[base_cols + date_cols]

    st.markdown(
        f'<div style="color:{TEXT_DIM};font-size:10px;margin-bottom:2px;'
        f'display:flex;justify-content:space-between;align-items:center;">'
        f'<span>共 {len(show)} 只 · {len(date_cols)} 天</span>'
        f'<span>🟥超强 🟧强势 🟨涨中 ⬜横盘 🟦跌中 🟫下跌</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="compact-table-wrap">'
        f'{show.to_html(escape=False, index=False, border=0, classes="compact-table")}'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown(f"""
    <style>
      .compact-table-wrap {{
        max-height: 320px; overflow-y: auto; overflow-x: auto;
        border-radius: 4px; border: 1px solid {BORDER}; background: {BG_PANEL};
      }}
      .compact-table-wrap::-webkit-scrollbar {{ width: 3px; height: 3px; }}
      .compact-table-wrap::-webkit-scrollbar-track {{ background: {BG_PANEL}; }}
      .compact-table-wrap::-webkit-scrollbar-thumb {{ background: {BORDER_HI}; border-radius: 1px; }}
      .compact-table {{ width: 100%; border-collapse: collapse; }}
      .compact-table thead {{ position: sticky; top: 0; z-index: 2; }}
      .compact-table th {{
        background: {BG_PANEL_HI}; color: {TEXT_DIM};
        font-weight: 500; font-size: 7px;
        text-align: center !important; padding: 2px 2px;
        border-bottom: 1px solid {BORDER};
        text-transform: uppercase; letter-spacing: 0.5px;
        white-space: nowrap;
      }}
      .compact-table th:nth-child(-n+2) {{ text-align: left !important; padding-left: 4px; }}
      .compact-table td {{
        padding: 1px 2px; border-bottom: 1px solid #0f1420;
        color: {TEXT}; white-space: nowrap;
        font-size: 9px; text-align: center;
        line-height: 1.2;
      }}
      .compact-table td:nth-child(-n+2) {{ text-align: left !important; padding-left: 4px; }}
      .compact-table th:first-child, .compact-table td:first-child {{
        position: sticky; left: 0; z-index: 3;
        min-width: 48px;
      }}
      .compact-table th:first-child {{ background: #141b2a; z-index: 4; }}
      .compact-table td:first-child {{ background: #0f141f; }}
      .compact-table tr:hover td:first-child {{ background: {BG_PANEL_HI}; }}
      .compact-table tr:hover td {{ background: #141b28; }}
      .compact-table tr:last-child td {{ border-bottom: none; }}
    </style>
    """, unsafe_allow_html=True)


def _compact_cell_html(label: str) -> str:
    """紧凑色块:只显示缩略文字(2-3字),更小 padding,更紧凑"""
    if not label or pd.isna(label) or label == "":
        return '<span style="color:#54586b">—</span>'
    bg, fg = LABEL_COLORS.get(label, ("#3a4156", "#fff"))
    # 缩略映射
    short_map = {
        "超强势": "超强",
        "强势": "强势",
        "震荡上涨": "涨中",
        "横盘震荡": "横盘",
        "震荡下跌": "跌中",
        "一直下跌": "下跌",
    }
    short = short_map.get(label, label[:2])
    return (
        f'<span style="'
        f'background:{bg};color:{fg};'
        f'padding:1px 4px;border-radius:2px;'
        f'font-size:10px;font-weight:600;'
        f'display:inline-block;text-align:center;'
        f'white-space:nowrap;line-height:1.5;'
        f'min-width:22px;">{short}</span>'
    )


def render_list_view(df_res: pd.DataFrame, label_filter: str | None = None):
    """详细列表子视图

    Args:
        df_res: 全部 ETF 数据
        label_filter: 可选,只展示指定趋势分类(如 "超强势")。None = 全部展示
    """
    if label_filter is not None:
        df_view = df_res[df_res["strength_label"] == label_filter].copy()
        df_view = sort_df(df_view, "strength_label", "desc")
        title_extra = f" · {label_filter}"
    else:
        df_view = sort_df(df_res, "strength_label", "desc")
        title_extra = ""

    c1, c2 = st.columns([1, 3])
    with c1:
        st.markdown(
            f'<div style="background:{BG_PANEL};border:1px solid {BORDER};'
            f'border-radius:6px;padding:6px 10px;text-align:center;">'
            f'<div style="color:{TEXT_MUTED};font-size:10px;text-transform:uppercase;'
            f'letter-spacing:0.5px;">标的池</div>'
            f'<div style="color:{TEXT};font-size:16px;font-weight:700;'
            f'font-family:monospace;">{len(df_view):,}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with c2:
        top = df_view.iloc[0] if len(df_view) else None
        if top is not None:
            bg, _ = LABEL_COLORS.get(top["strength_label"], ("#fff", "#fff"))
            st.markdown(
                f'<div style="text-align:right;color:{TEXT_DIM};font-size:10px;'
                f'padding:4px 0;">'
                f'<span style="color:{TEXT_MUTED};">排序首位</span> '
                f'<span style="color:{TEXT};font-size:13px;font-weight:600;'
                f'font-family:monospace;">{top["code"]} {top["name"]}</span> '
                f'<span style="color:{bg};font-weight:600;font-size:10px;">'
                f'· {top["strength_label"]}</span></div>',
                unsafe_allow_html=True,
            )
    st.markdown(f'<div style="height:6px"></div>', unsafe_allow_html=True)
    if df_view.empty:
        st.info(f"该分类下暂无 ETF{title_extra}")
    else:
        render_table(df_view)


def kpi_card(title: str, value: str, sub: str, color: str, hover_color: str | None = None) -> str:
    """统一 KPI 卡(紧凑版)"""
    return (
        f'<div class="kpi-card" '
        f'style="background:{BG_PANEL};border:1px solid {BORDER};'
        f'border-radius:8px;padding:8px 10px;height:66px;'
        f'transition:all 0.15s ease;cursor:default;">'
        f'<div style="color:{TEXT_MUTED};font-size:9px;font-weight:500;'
        f'letter-spacing:0.5px;text-transform:uppercase;'
        f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{title}</div>'
        f'<div class="kpi-value" style="color:{color};font-size:18px;font-weight:700;'
        f'font-family:monospace;margin-top:2px;line-height:1.15;'
        f'font-feature-settings:&quot;tnum&quot;;">{value}</div>'
        f'<div style="color:{TEXT_DIM};font-size:9px;margin-top:1px;'
        f'font-family:monospace;">{sub}</div>'
        f'</div>'
    )


def render_kpi(df: pd.DataFrame):
    """ETF 强弱趋势 Tab 专属 KPI:标的池 + 6 档趋势分布"""
    total_size = df["fund_size_yi"].sum() if "fund_size_yi" in df.columns else 0
    n_total = len(df)

    # 注入 KPI 样式
    st.markdown(f"""
    <style>
      .kpi-card:hover {{
        background:{BG_PANEL_HI} !important;
        border-color:{BORDER_HI} !important;
        transform: translateY(-1px);
      }}
      .kpi-card:hover .kpi-value {{
        filter: brightness(1.2);
      }}
      /* KPI 列间距压紧 */
      .row-widget.stHorizontal {{ gap: 4px !important; }}
    </style>
    """, unsafe_allow_html=True)

    cols = st.columns(7, gap="small")
    # 总计
    with cols[0]:
        st.markdown(kpi_card(
            "标的池",
            f"{n_total:,}",
            f"总规模 {total_size:,.1f} 亿元",
            color=TEXT,
        ), unsafe_allow_html=True)
    # 6 档趋势
    for i, label in enumerate(LABEL_ORDER, start=1):
        sub = df[df["strength_label"] == label]
        count = len(sub)
        size = sub["fund_size_yi"].sum() if "fund_size_yi" in sub.columns else 0
        pct = count / n_total * 100 if n_total > 0 else 0
        bg, _ = LABEL_COLORS[label]
        with cols[i]:
            st.markdown(kpi_card(
                label,
                f"{count}",
                f"{size:,.1f} 亿 · {pct:.1f}%",
                color=bg,
            ), unsafe_allow_html=True)


def render(df_res: pd.DataFrame, df_hist: pd.DataFrame):
    """ETF 强弱趋势 Tab 入口(被 tabs/__init__.py 调用)

    当前结构: 1 个 ETF 强弱 Tab + 内部 KPI + 2 个子视图
    后续如果要把这两个子视图升级成独立顶层 Tab,改这里即可
    """
    # KPI 只在 ETF Tab 内显示(其他 Tab 不要标的池/趋势分布这些 ETF 专用指标)
    render_kpi(df_res)
    st.markdown(f'<div style="height:16px"></div>', unsafe_allow_html=True)

    # 6 个趋势分类子 Tab + 趋势演变
    icons = {
        "超强势":   "🟥",
        "强势":     "🟧",
        "震荡上涨": "🟨",
        "横盘震荡": "⬜",
        "震荡下跌": "🟦",
        "一直下跌": "🟫",
    }
    short_tab_labels = ["超强势", "强势", "震荡上涨", "横盘震荡", "震荡下跌", "一直下跌"]
    labels = [f"{icons.get(l, '')} {s}" for l, s in zip(LABEL_ORDER, short_tab_labels)] + ["🔥 趋势演变"]
    sub_tabs = st.tabs(labels)
    # 内层子 Tab 紧凑样式(用更渐进的选择器避免覆盖顶层 Tab)
    st.markdown(f"""
    <style>
      .stTabs .stTabs [data-baseweb="tab-list"] {{
        gap: 2px !important;
      }}
      .stTabs .stTabs [data-baseweb="tab"] {{
        font-size: 10px !important;
        padding: 0 8px !important;
        height: 30px !important;
      }}
    </style>
    """, unsafe_allow_html=True)
    for i, label in enumerate(LABEL_ORDER):
        with sub_tabs[i]:
            render_list_view(df_res, label_filter=label)
    with sub_tabs[-1]:
        render_history_table(df_hist, df_res)
