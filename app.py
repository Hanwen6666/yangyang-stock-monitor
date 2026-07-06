"""
羊羊股市监测 — 主入口

架构:
  app.py            ← 入口:页面配置、顶部 Header、KPI、Tab 路由
  tabs/             ← 各业务 Tab
    etf_strength.py ← ETF 强弱趋势(当前已实现)
    placeholder.py  ← 占位 Tab(演示新 Tab 怎么加)
    <new_tab>.py    ← 新增 Tab 放这里

新 Tab 添加步骤:见 tabs/placeholder.py 末尾注释
"""
import streamlit as st
import pandas as pd
from pathlib import Path
import sys
import datetime

# 把当前目录加进 path,这样 tabs/ 能 import
sys.path.insert(0, str(Path(__file__).parent))
from tabs import render_all_tabs, TABS  # noqa: E402

# ============================================================
# 主题色(全局常量,各 Tab 通过 sys.path 引用 app.py 的常量)
# ============================================================
BG          = "#0a0e1a"
BG_PANEL    = "#131826"
BG_PANEL_HI = "#1a2030"
BORDER      = "#1f2638"
BORDER_HI   = "#2a334a"
TEXT        = "#e8eaef"
TEXT_MUTED  = "#7a7f96"
TEXT_DIM    = "#54586b"
ACCENT_UP   = "#ff4d4f"
ACCENT_DN   = "#00d4aa"

LABEL_COLORS = {
    "超强势":   ("#ff3b5c", "#ffffff"),
    "强势":     ("#ff7800", "#ffffff"),
    "震荡上涨": ("#ffcc00", "#0a0e1a"),
    "横盘震荡": ("#3a4156", "#c5c8d6"),
    "震荡下跌": ("#4a90d9", "#ffffff"),
    "一直下跌": ("#1f3556", "#7a8aa8"),
}
LABEL_ORDER = ["超强势", "强势", "震荡上涨", "横盘震荡", "震荡下跌", "一直下跌"]

# ============================================================
# 页面配置
# ============================================================
st.set_page_config(
    page_title="羊羊股市监测",
    page_icon="🐑",
    layout="wide",
    initial_sidebar_state="auto",
    menu_items={"About": "羊羊股市监测 · 多 Tab 股市分析"},
)

# ============================================================
# 全局 CSS
# ============================================================
st.markdown(f"""
<style>
  .stApp {{ background: {BG}; color: {TEXT}; }}
  [data-testid="stHeader"] {{ background: transparent !important; }}
  [data-testid="stToolbar"] {{ display: none; }}
  #MainMenu {{ visibility: hidden; }}
  footer {{ visibility: hidden; }}

  html, body, [class*="css"] {{
    font-family: -apple-system, "SF Pro SC", "PingFang SC", "Microsoft YaHei", sans-serif;
    color: {TEXT};
  }}
  code, pre, .mono {{
    font-family: "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
    font-feature-settings: "tnum";
  }}

  .block-container {{ padding-top: 2rem; padding-bottom: 2rem; }}

  /* Tabs */
  .stTabs [data-baseweb="tab-list"] {{
    gap: 4px;
    background: transparent;
    border-bottom: 1px solid {BORDER};
  }}
  .stTabs [data-baseweb="tab"] {{
    height: 44px;
    background: transparent;
    color: {TEXT_MUTED};
    border-radius: 6px 6px 0 0;
    font-weight: 500;
    padding: 0 18px;
    font-size: 14px;
  }}
  .stTabs [aria-selected="true"] {{
    background: {BG_PANEL};
    color: {TEXT};
    border-bottom: 2px solid {ACCENT_UP};
  }}

  /* Metric */
  [data-testid="stMetricValue"] {{
    font-size: 26px;
    font-weight: 600;
    color: {TEXT};
    font-family: "SF Mono", monospace;
    font-feature-settings: "tnum";
  }}
  [data-testid="stMetricLabel"] {{
    color: {TEXT_MUTED};
    font-size: 11px;
    font-weight: 500;
  }}
</style>
""", unsafe_allow_html=True)

# ============================================================
# 数据加载
# ============================================================
DATA_DIR = Path(__file__).parent / "data"
ASOF_FILE = DATA_DIR / ".asof"

@st.cache_data(ttl=300)
def load_results() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "results.csv")

@st.cache_data(ttl=300)
def load_history() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "etf_trend_history.csv")

# ============================================================
# 顶部 Header
# ============================================================
def render_header(df: pd.DataFrame):
    asof = df["asof_date"].iloc[0] if "asof_date" in df.columns and len(df) else "—"
    if isinstance(asof, str) and len(asof) == 8 and asof.isdigit():
        asof_str = f"{asof[:4]}-{asof[4:6]}-{asof[6:]}"
    else:
        asof_str = str(asof)
    n = len(df)
    tab_count = len(TABS)

    st.markdown(f"""
    <div style="display:flex;align-items:baseline;gap:16px;margin-bottom:4px;
                padding-bottom:16px;border-bottom:1px solid {BORDER};">
      <div style="display:flex;align-items:baseline;gap:10px;">
        <h1 style="margin:0;font-size:26px;font-weight:700;color:{TEXT};
                   letter-spacing:-0.5px;">
          羊羊股市监测
        </h1>
        <span style="color:{TEXT_MUTED};font-size:14px;font-weight:400;">
          多 Tab 股市分析
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
        <div>
          <span style="color:{TEXT_DIM};font-size:11px;">功能</span>
          <span style="color:{TEXT_MUTED};font-size:13px;font-weight:600;
                       font-family:monospace;margin-left:6px;">{tab_count} 个 Tab</span>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

# ============================================================
# 顶部 KPI(只对 ETF 强弱 Tab 显示)
# ============================================================
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
    st.markdown(f'<div style="height:16px"></div>', unsafe_allow_html=True)

    # 路由所有 Tab
    # 各 Tab 内部决定是否要展示自己的 KPI(例如 ETF 强弱 Tab 会在内部显示标的池/趋势分布)
    render_all_tabs(df_res, df_hist)

    st.markdown(f"""
    <div style="margin-top:24px;padding-top:16px;border-top:1px solid {BORDER};
                color:{TEXT_DIM};font-size:11px;text-align:center;">
      🐑 羊羊股市监测 · 数据每 5 分钟缓存 ·
      数据源:CloudBase API + AmazingData
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
