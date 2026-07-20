"""
Tabs 模块 — 业务 Tab 集合

每个 Tab 是独立文件,导出 render(df_res, df_hist) 函数
新增 Tab 步骤:
  1. 在 tabs/ 下新建文件,例如 tabs/market_overview.py
  2. 实现 def render(df_res, df_hist): ...
  3. 在 tabs/__init__.py 的 TABS 列表里加一项
  4. app.py 的 st.tabs() 调用会自动出现新 Tab
"""
import streamlit as st

from . import etf_strength  # noqa: F401
from . import strength_rotation  # noqa: F401

# Tab 注册表 — 新 Tab 加这里即可
TABS = [
    {
        "key": "etf_overview",
        "label": "大盘总览",
        "module": etf_strength,
        "render": "render_overview",
        "icon": "📊",
    },
    {
        "key": "etf_history",
        "label": "趋势演变",
        "module": etf_strength,
        "render": "render_history",
        "icon": "🔥",
    },
    {
        "key": "strength_rotation",
        "label": "强势股轮动",
        "module": strength_rotation,
        "render": "render",
        "icon": "🎯",
    },
    {
        "key": "etf_individual",
        "label": "个股分析",
        "module": etf_strength,
        "render": "render_individual",
        "icon": "📈",
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
            # 兼容两种调用约定：
            # 1. spec["render"] 是个字符串，调用 module 里对应函数名
            # 2. module.render(df_res, df_hist) 兼容老版本
            mod = spec["module"]
            render_fn_name = spec.get("render")
            if render_fn_name and hasattr(mod, render_fn_name):
                getattr(mod, render_fn_name)(df_res, df_hist)
            elif hasattr(mod, "render"):
                mod.render(df_res, df_hist)
            else:
                st.error(f"Tab '{spec['key']}' 没有 render 函数")
