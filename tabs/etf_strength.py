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

    st.caption(f"共 {len(show)} 只 · 默认按趋势强度排序 · 表内可滚动")
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
        font-size: 11px; text-align: left; padding: 12px 14px;
        border-bottom: 1px solid {BORDER}; text-transform: uppercase;
        letter-spacing: 0.5px; white-space: nowrap;
      }}
      .etf-table td {{
        padding: 12px 14px; border-bottom: 1px solid {BORDER};
        color: {TEXT}; white-space: nowrap; font-feature-settings: "tnum";
      }}
      .etf-table tr:hover td {{ background: {BG_PANEL_HI}; }}
      .etf-table tr:last-child td {{ border-bottom: none; }}
      .etf-table td:nth-child(3) {{ white-space: normal; }}
    </style>
    """, unsafe_allow_html=True)


def render_history_heatmap(df_hist: pd.DataFrame, df_res: pd.DataFrame):
    if df_hist.empty or df_res.empty:
        st.info("暂无趋势历史数据")
        return

    points = [c for c in df_hist.columns if c not in ("code", "name")]
    if not points:
        st.info("无趋势数据点")
        return

    df_show = df_hist.head(50).copy()
    n_show = min(15, len(points))
    points_show = list(reversed(points))[:n_show]

    LABEL_NUM = {l: i for i, l in enumerate(LABEL_ORDER)}
    z = []
    text = []
    for _, row in df_show.iterrows():
        z_row, t_row = [], []
        for p in points_show:
            v = row[p] if pd.notna(row[p]) else ""
            z_row.append(LABEL_NUM.get(v, -1))
            t_row.append(v if v else "—")
        z.append(z_row)
        text.append(t_row)

    fig = go.Figure(data=go.Heatmap(
        z=z, text=text, texttemplate="%{text}",
        textfont={"size": 10, "color": "#fff", "family": "PingFang SC"},
        colorscale=[
            [0.0,  LABEL_COLORS["一直下跌"][0]],
            [0.2,  LABEL_COLORS["震荡下跌"][0]],
            [0.4,  LABEL_COLORS["横盘震荡"][0]],
            [0.6,  LABEL_COLORS["震荡上涨"][0]],
            [0.8,  LABEL_COLORS["强势"][0]],
            [1.0,  LABEL_COLORS["超强势"][0]],
        ],
        zmin=0, zmax=5, showscale=False,
        xgap=2, ygap=2,
        hovertemplate="<b>%{y}</b><br>%{x}<br>趋势:%{text}<extra></extra>",
    ))

    x_disp = []
    for p in points_show:
        date_str = p.split("_")[1] if "_" in p else p
        if "-" in date_str and len(date_str) == 10:
            x_disp.append(f"{date_str[5:7]}/{date_str[8:10]}")
        else:
            x_disp.append(date_str)

    fig.update_layout(
        paper_bgcolor=BG_PANEL, plot_bgcolor=BG_PANEL,
        font={"color": TEXT, "family": "Inter, -apple-system, sans-serif", "size": 12},
        height=max(400, len(df_show) * 22 + 80),
        xaxis=dict(side="top", tickmode="array", tickvals=x_disp, ticktext=x_disp,
                   showgrid=False, zeroline=False, tickfont=dict(color=TEXT_MUTED, size=10)),
        yaxis=dict(autorange="reversed", tickmode="array",
                   tickvals=list(range(len(df_show))),
                   ticktext=[f'{r["code"]} {r["name"]}' for _, r in df_show.iterrows()],
                   showgrid=False, zeroline=False,
                   tickfont=dict(color=TEXT, size=11, family="monospace")),
        margin=dict(t=40, r=20, b=20, l=200),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        f"共 {len(df_show)} 只(超 50 请缩小筛选) · "
        f"展示近 {n_show} 天 · "
        f"日期从左(最新)到右(最远) · "
        f"色块🟥超强势 🟧强势 🟨震荡上涨 ⬜横盘震荡 🟦震荡下跌 🟫一直下跌"
    )


def render_list_view(df_res: pd.DataFrame):
    """子视图 1:详细列表"""
    df_view = sort_df(df_res, "strength_label", "desc")

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
    render_table(df_view)


def render_kpi(df: pd.DataFrame):
    """ETF 强弱趋势 Tab 专属 KPI:标的池 + 6 档趋势分布"""
    total_size = df["fund_size_yi"].sum() if "fund_size_yi" in df.columns else 0
    cols = st.columns(7, gap="small")
    with cols[0]:
        st.markdown(
            f'<div style="background:{BG_PANEL};border:1px solid {BORDER};'
            f'border-radius:8px;padding:16px 18px;height:96px;">'
            f'<div style="color:{TEXT_MUTED};font-size:11px;font-weight:500;'
            f'letter-spacing:0.5px;text-transform:uppercase;">标的池 / 总规模</div>'
            f'<div style="color:{TEXT};font-size:28px;font-weight:600;'
            f'font-family:monospace;margin-top:6px;line-height:1.2;">{len(df):,}</div>'
            f'<div style="color:{TEXT_DIM};font-size:11px;margin-top:4px;">'
            f'{total_size:,.1f} 亿元</div></div>',
            unsafe_allow_html=True,
        )
    for i, label in enumerate(LABEL_ORDER, start=1):
        sub = df[df["strength_label"] == label]
        count = len(sub)
        size = sub["fund_size_yi"].sum() if "fund_size_yi" in sub.columns else 0
        pct = count / len(df) * 100 if len(df) > 0 else 0
        bg, _ = LABEL_COLORS[label]
        with cols[i]:
            st.markdown(
                f'<div style="background:{BG_PANEL};border:1px solid {BORDER};'
                f'border-radius:8px;padding:16px 18px;height:96px;">'
                f'<div style="color:{TEXT_MUTED};font-size:11px;font-weight:500;'
                f'letter-spacing:0.5px;text-transform:uppercase;">{label}</div>'
                f'<div style="color:{bg};font-size:28px;font-weight:600;'
                f'font-family:monospace;margin-top:6px;line-height:1.2;">{count}</div>'
                f'<div style="color:{TEXT_DIM};font-size:11px;margin-top:4px;">'
                f'{size:,.1f} 亿 · {pct:.1f}%</div></div>',
                unsafe_allow_html=True,
            )


def render(df_res: pd.DataFrame, df_hist: pd.DataFrame):
    """ETF 强弱趋势 Tab 入口(被 tabs/__init__.py 调用)

    当前结构: 1 个 ETF 强弱 Tab + 内部 KPI + 2 个子视图
    后续如果要把这两个子视图升级成独立顶层 Tab,改这里即可
    """
    # KPI 只在 ETF Tab 内显示(其他 Tab 不要标的池/趋势分布这些 ETF 专用指标)
    render_kpi(df_res)
    st.markdown(f'<div style="height:16px"></div>', unsafe_allow_html=True)

    sub1, sub2 = st.tabs(["📋 详细列表", "🔥 趋势演变"])
    with sub1:
        render_list_view(df_res)
    with sub2:
        st.markdown(
            f'<p style="color:{TEXT_MUTED};font-size:13px;margin:0 0 8px 0">'
            f'🔥 近 25 天趋势演变(色块化热力图)</p>',
            unsafe_allow_html=True,
        )
        render_history_heatmap(df_hist, df_res)
