"""
Tabs 模块 — 业务 Tab 集合

每个 Tab 是独立文件,导出 render(df_res, df_hist) 函数
新增 Tab 步骤:
  1. 在 tabs/ 下新建文件,例如 tabs/market_overview.py
  2. 实现 def render(df_res, df_hist): ...
  3. 在 tabs/__init__.py 的 TABS 列表里加一项
  4. 在 app.py 的 st.tabs() 调用会自动出现新 Tab
"""
import streamlit as st

from . import etf_strength, placeholder  # noqa: F401

# Tab 注册表 — 新 Tab 加这里即可
TABS = [
    {
        "key": "etf_strength",
        "label": "ETF 强弱趋势",
        "module": etf_strength,
        "icon": "📈",
    },
    {
        "key": "placeholder",
        "label": "更多功能",
        "module": placeholder,
        "icon": "🧩",
    },
]


def render_all_tabs(df_res, df_hist):
    """由 app.py 调用:渲染所有注册过的 Tab"""
    if not TABS:
        st.warning("暂无可用 Tab,请联系管理员")
        return

    labels = [f"{t['icon']} {t['label']}" for t in TABS]
    tabs = st.tabs(labels)
    for tab, spec in zip(tabs, TABS):
        with tab:
            spec["module"].render(df_res, df_hist)
