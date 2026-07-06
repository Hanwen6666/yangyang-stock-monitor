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
        show["代码"] = show["代码"].apply(fmt_int)
    for col, prec in [("50日斜率", 2), ("20日斜率", 2), ("120日斜率", 2),
                       ("夏普", 3), ("ADX", 2), ("样本", 0)]:
        if col in show.columns:
            show[col] = show[col].apply(lambda v, p=prec: fmt_num(v, p))
    if "60日↑%" in show.columns:
        show["60日↑%"] = show["60日↑%"].apply(fmt_pct_val)
    if "规模(亿)" in show.columns:
        show["规模(亿)"] = show["规模(亿)"].apply(fmt_yi)

    st.caption(f"共 {len(show)} 只 · 按趋势强度排序 · 表内可滚动")
    st.markdown(
        f'<div class="etf-table-wrap">'
        f'{show.to_html(escape=False, index=False, border=0, classes="etf-table")}'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown(f"""
    <style>
      .etf-table-wrap {{
        max-height: 720px; overflow-y: auto; overflow-x: auto;
        border-radius: 8px; border: 1px solid {BORDER}; background: {BG_PANEL};
      }}
      .etf-table-wrap::-webkit-scrollbar {{ width: 8px; height: 8px; }}
      .etf-table-wrap::-webkit-scrollbar-track {{ background: {BG_PANEL}; }}
      .etf-table-wrap::-webkit-scrollbar-thumb {{ background: {BORDER_HI}; border-radius: 4px; }}
      .etf-table-wrap::-webkit-scrollbar-thumb:hover {{ background: {TEXT_DIM}; }}
      .etf-table {{
        width: 100%; border-collapse: collapse;
        font-family: "SF Mono", monospace; font-size: 13px; background: {BG_PANEL};
        border-radius: 8px;
      }}
      .etf-table th {{
        background: {BG_PANEL_HI}; color: {TEXT_MUTED}; font-weight: 500;
        font-size: 10px; text-align: left; padding: 10px 14px;
        border-bottom: 1px solid {BORDER}; text-transform: uppercase;
        letter-spacing: 0.6px; white-space: nowrap;
        position: sticky; top: 0; z-index: 1;
      }}
      .etf-table td {{
        padding: 10px 14px; border-bottom: 1px solid {BORDER};
        color: {TEXT}; white-space: nowrap; font-feature-settings: "tnum";
        font-size: 13px;
      }}
      .etf-table tr:hover td {{ background: {BG_PANEL_HI}; }}
      .etf-table tr:last-child td {{ border-bottom: none; }}
      .etf-table td:nth-child(3) {{ white-space: normal; }}
    </style>
    """, unsafe_allow_html=True)


def render_history_table(df_hist: pd.DataFrame, df_res: pd.DataFrame):
    """趋势演变子视图 — 表格形式 + 筛选 + 全部 ETF

    列: 代码 / 名称 / 分类 / [25 天趋势标签] / 最新趋势
    """
    if df_hist.empty or df_res.empty:
        st.info("暂无趋势历史数据")
        return

    points = [c for c in df_hist.columns if c not in ("code", "name")]
    if not points:
        st.info("无趋势数据点")
        return

    # 把分类(从 results)拼接到 history 表上
    cat_map = dict(zip(df_res["code"], df_res["category"]))
    latest_label_map = dict(zip(df_res["code"], df_res["strength_label"]))
    df = df_hist.copy()
    df["分类"] = df["code"].map(cat_map).fillna("—")
    df["最新"] = df["code"].map(latest_label_map).fillna("—")

    # === 筛选区 ===
    fc1, fc2, fc3 = st.columns([1, 1, 2])
    with fc1:
        cats = sorted(df["分类"].dropna().unique().tolist())
        cat_filter = st.multiselect(
            "行业分类", cats, default=[],
            placeholder="全部(不选即全部)",
            label_visibility="collapsed",
        )
    with fc2:
        label_filter = st.multiselect(
            "当前趋势", LABEL_ORDER, default=[],
            placeholder="全部(不选即全部)",
            label_visibility="collapsed",
        )
    with fc3:
        search = st.text_input(
            "🔍 搜索", "", placeholder="代码或名称模糊搜索", label_visibility="collapsed"
        )

    # 应用筛选
    if cat_filter:
        df = df[df["分类"].isin(cat_filter)]
    if label_filter:
        df = df[df["最新"].isin(label_filter)]
    if search:
        s = search.lower()
        df = df[df["code"].astype(str).str.contains(s, case=False, na=False) |
                df["name"].astype(str).str.contains(s, case=False, na=False)]

    if df.empty:
        st.info("无匹配 ETF")
        return

    # === 构建展示表 ===
    # 日期列名 d_2026-07-03 → 07-03 (从最新到最远)
    points_disp = list(reversed(points))  # 最新在左
    col_rename = {p: (p.split("_")[1][5:10].replace("-", "-") if "_" in p else p)
                  for p in points_disp}

    # 先重命名所有点列(避免下面再用原始 key 找不到)
    show = df[["code", "name", "分类", "最新"] + points].copy()
    show = show.rename(columns={"code": "代码", "name": "名称"})
    show = show.rename(columns=col_rename)
    # 最新 → 色块
    show["最新"] = show["最新"].apply(label_badge_html)
    # 每天的趋势 → 色块 (现在 show 的列名已是重命名后的)
    for orig_p, disp_col in col_rename.items():
        if disp_col in show.columns:
            show[disp_col] = show[disp_col].apply(_history_cell_html)

    # 列顺序
    base_cols = ["代码", "名称", "分类", "最新"]
    date_cols = list(col_rename.values())
    show = show[base_cols + date_cols]

    st.caption(
        f"共 {len(show)} 只 · "
        f"日期从左(最新 7/3)到右(最远 5/29) · "
        f"共 {len(date_cols)} 天 · "
        f"色块🟥超强势 🟧强势 🟨震荡上涨 ⬜横盘震荡 🟦震荡下跌 🟫一直下跌"
    )
    st.markdown(
        f'<div class="etf-table-wrap">'
        f'{show.to_html(escape=False, index=False, border=0, classes="etf-table history-table")}'
        f'</div>',
        unsafe_allow_html=True,
    )
    # 给历史表格加专门的样式: 日期列用色块
    st.markdown(f"""
    <style>
      .history-table th {{ min-width: 36px; text-align: center !important; }}
      .history-table td {{ text-align: center; }}
      .history-table td:first-child,
      .history-table td:nth-child(2),
      .history-table td:nth-child(3),
      .history-table td:nth-child(4) {{ text-align: left; }}
    </style>
    """, unsafe_allow_html=True)


def _history_cell_html(label: str) -> str:
    """历史表单元格:色块 + 文字"""
    if not label or pd.isna(label) or label == "":
        return '<span style="color:#54586b">—</span>'
    bg, fg = LABEL_COLORS.get(label, ("#3a4156", "#fff"))
    # 缩写到单字 + 全称
    short = {
        "超强势": "超", "强势": "强", "震荡上涨": "涨",
        "横盘震荡": "横", "震荡下跌": "跌", "一直下跌": "下",
    }.get(label, "—")
    return (
        f'<span title="{label}" style="'
        f'background:{bg};color:{fg};'
        f'padding:2px 6px;border-radius:3px;'
        f'font-size:11px;font-weight:600;'
        f'display:inline-block;min-width:14px;text-align:center;'
        f'cursor:help;">{short}</span>'
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

    c1, c2 = st.columns([1, 2])
    with c1: st.metric("标的池", f"{len(df_view):,} 只")
    with c2:
        top = df_view.iloc[0] if len(df_view) else None
        if top is not None:
            bg, _ = LABEL_COLORS.get(top["strength_label"], ("#fff", "#fff"))
            st.markdown(
                f'<div style="text-align:right;color:{TEXT_MUTED};font-size:12px;line-height:1.5">'
                f'排序首位:<br>'
                f'<b style="color:{TEXT};font-size:15px;font-family:monospace">'
                f'{top["code"]} {top["name"]}</b> · '
                f'<span style="color:{bg};font-weight:600">'
                f'{top["strength_label"]}</span></div>',
                unsafe_allow_html=True,
            )
    st.markdown(f'<div style="height:12px"></div>', unsafe_allow_html=True)
    if df_view.empty:
        st.info(f"该分类下暂无 ETF{title_extra}")
    else:
        render_table(df_view)


def kpi_card(title: str, value: str, sub: str, color: str, hover_color: str | None = None) -> str:
    """统一 KPI 卡。hover_color:鼠标悬停时主数字颜色"""
    return (
        f'<div class="kpi-card" '
        f'style="background:{BG_PANEL};border:1px solid {BORDER};'
        f'border-radius:10px;padding:14px 16px;height:88px;'
        f'transition:all 0.2s ease;cursor:default;">'
        f'<div style="color:{TEXT_MUTED};font-size:11px;font-weight:500;'
        f'letter-spacing:0.5px;text-transform:uppercase;'
        f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{title}</div>'
        f'<div class="kpi-value" style="color:{color};font-size:26px;font-weight:600;'
        f'font-family:monospace;margin-top:4px;line-height:1.15;'
        f'font-feature-settings:&quot;tnum&quot;;">{value}</div>'
        f'<div style="color:{TEXT_DIM};font-size:11px;margin-top:3px;'
        f'font-family:monospace;">{sub}</div>'
        f'</div>'
    )


def render_kpi(df: pd.DataFrame):
    """ETF 强弱趋势 Tab 专属 KPI:标的池 + 6 档趋势分布"""
    total_size = df["fund_size_yi"].sum() if "fund_size_yi" in df.columns else 0
    n_total = len(df)

    # 注入 hover 样式
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
    labels = [f"{icons.get(l, '')} {l}" for l in LABEL_ORDER] + ["🔥 趋势演变"]
    sub_tabs = st.tabs(labels)
    for i, label in enumerate(LABEL_ORDER):
        with sub_tabs[i]:
            render_list_view(df_res, label_filter=label)
    with sub_tabs[-1]:
        render_history_table(df_hist, df_res)
