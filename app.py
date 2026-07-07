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
import time

# 把当前目录加进 path,这样 tabs/ 能 import
sys.path.insert(0, str(Path(__file__).parent))
from lib.constants import (  # noqa: E402
    BG, BG_PANEL, BG_PANEL_HI, BORDER, BORDER_HI,
    TEXT, TEXT_MUTED, TEXT_DIM, ACCENT_UP, ACCENT_DN,
    LABEL_COLORS, LABEL_ORDER,
)
from tabs import render_all_tabs, TABS  # noqa: E402
from fetch_data import refresh_data, recompute_locally, DATA_DIR as FETCH_DATA_DIR  # noqa: E402

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
    overflow-x: auto;
    overflow-y: hidden;
    white-space: nowrap;
    scrollbar-width: thin;
  }}
  .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar {{ height: 4px; }}
  .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar-thumb {{ background: {BORDER_HI}; border-radius: 2px; }}
  .stTabs [data-baseweb="tab"] {{
    height: 40px;
    background: transparent;
    color: {TEXT_MUTED};
    border-radius: 6px 6px 0 0;
    font-weight: 500;
    padding: 0 14px;
    font-size: 13px;
    flex-shrink: 0;
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
    p = DATA_DIR / "results.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    # 兼容旧 CSV 缺少 latest_close/latest_volume 字段
    for col in ["latest_close", "latest_volume", "fund_size_yi"]:
        if col not in df.columns:
            df[col] = 0
    return df

@st.cache_data(ttl=300)
def load_history() -> pd.DataFrame:
    p = DATA_DIR / "etf_trend_history.csv"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p)

# ============================================================
# 顶部 Header
# ============================================================
def render_header(df: pd.DataFrame, refresh_state: dict | None = None):
    asof = df["asof_date"].iloc[0] if "asof_date" in df.columns and len(df) else refresh_state.get("asof_date", "—") if refresh_state else "—"
    if isinstance(asof, str) and len(asof) == 8 and asof.isdigit():
        asof_str = f"{asof[:4]}-{asof[4:6]}-{asof[6:]}"
    else:
        asof_str = str(asof)
    n = len(df)

    # 上次刷新时间显示
    last_fetch_html = ""
    if refresh_state and refresh_state.get("fetched_at"):
        fa = refresh_state["fetched_at"]
        try:
            t = fa.split("T")[1].split(".")[0] if "T" in fa else fa.split(" ")[1]
            last_fetch_html = (
                f'<span style="color:{BORDER_HI};">|</span>'
                f'<span style="color:{TEXT_DIM};font-size:11px;">刷新</span>'
                f'<span style="color:{TEXT_MUTED};font-size:12px;font-family:monospace;">{t}</span>'
            )
        except Exception:
            last_fetch_html = f'<span style="color:{TEXT_DIM};font-size:11px;">刷新 {fa}</span>'

    header_html = (
        '<div style="display:flex;align-items:center;gap:16px;margin-bottom:4px;'
        f'padding-bottom:14px;border-bottom:1px solid {BORDER};">'
      '<div style="display:flex;align-items:baseline;gap:10px;">'
        '<h1 style="margin:0;font-size:24px;font-weight:700;'
             f'color:{TEXT};letter-spacing:-0.5px;">羊羊股市监测</h1>'
        '<span style="color:{TEXT_DIM};font-size:12px;font-weight:400;'
              'letter-spacing:0.5px;margin-left:4px;">A 股 ETF · 趋势分析</span>'
      '</div>'
      '<div style="margin-left:auto;display:flex;align-items:center;gap:8px;">'
        '<div style="display:flex;align-items:center;gap:8px;'
             f'background:{BG_PANEL};border:1px solid {BORDER};'
             'border-radius:6px;padding:5px 12px;">'
          f'<span style="color:{TEXT_DIM};font-size:11px;">数据日期</span>'
          f'<span style="color:{TEXT};font-size:12px;font-weight:600;font-family:monospace;">{asof_str}</span>'
          f'<span style="color:{BORDER_HI};">|</span>'
          f'<span style="color:{TEXT_DIM};font-size:11px;">标的池</span>'
          f'<span style="color:{ACCENT_UP};font-size:12px;font-weight:600;font-family:monospace;">{n}</span>'
          + last_fetch_html +
        '</div>'
      '</div>'
    '</div>'
    )
    st.markdown(header_html, unsafe_allow_html=True)

# ============================================================
# 顶部 KPI(只对 ETF 强弱 Tab 显示)
# ============================================================
# ============================================================
# 主流程
# ============================================================
def main():
    # Session state
    if "refresh_state" not in st.session_state:
        st.session_state.refresh_state = None  # 上次刷新状态 dict

    # 顶部 header + 刷新按钮 并排
    # 这里只调一次 render_header 即可,刷新按钮接在 header 后面
    df_res = load_results()
    df_hist = load_history()

    # Fallback: 启动时 CSV 缺失 → 用仓库中的 seed CSV
    if df_res.empty or df_hist.empty:
        import shutil
        for src_name, dst_name in [
            ("_seed_results.csv", "results.csv"),
            ("_seed_history.csv", "etf_trend_history.csv"),
        ]:
            src = DATA_DIR / src_name
            dst = DATA_DIR / dst_name
            if src.exists() and not dst.exists():
                shutil.copy(src, dst)
        # 重读
        load_results.clear()
        load_history.clear()
        df_res = load_results()
        df_hist = load_history()

    is_empty = df_res.empty
    if is_empty:
        # 首次启动 — 只显示提示,不自动调 API 防夸
        st.markdown(
            '<div style="background:#1a1f2e;border:1px solid #f59e0b;'
            'border-radius:8px;padding:20px;text-align:center;margin:24px 0;">'
            '<div style="color:#f59e0b;font-size:18px;font-weight:600;margin-bottom:8px;">'
            '📊 暂无数据</div>'
            '<div style="color:#9ca3af;font-size:13px;">'
            '点击下方 <b>「数据刷新」</b> 按钮,拉取最新 ETF 数据并用 v27 算法重算(约 2 分钟)'
            '</div></div>',
            unsafe_allow_html=True,
        )

    # 渲染 header
    render_header(df_res if not is_empty else pd.DataFrame(), st.session_state.refresh_state)

    # 单按钮:点一下 → API 拉 history + v27 本地重算 + 进度条
    btn_col, info_col = st.columns([1, 9])
    with btn_col:
        clicked = st.button(
            "🔄 数据刷新",
            help="先拉最新趋势历史,再用 v27 算法重算当前指标(约 2 分钟)",
            use_container_width=True,
            type="primary",
        )
    with info_col:
        rs = st.session_state.refresh_state
        if clicked:
            t_start = time.time()
            progress = st.progress(0, text="拉取趋势历史...")

            # === Step 1: 拉 API 数据(秒级) ===
            api_res = refresh_data()
            if not api_res["ok"]:
                progress.empty()
                st.error(f"拉取数据失败: {api_res['error']}")
                st.stop()

            progress.progress(15, text="趋势历史已就绪,本地 v27 重算...")

            # === Step 2: 本地全量 v27 重算(用最新 K 线真实算) ===
            def on_progress(i, total, code, metrics, status):
                pct = 15 + int((i / total) * 80)
                phase = "拉 K 线" if status == "kline" else "重算"
                progress.progress(min(pct, 99) / 100,
                                   text=f"{phase} {i}/{total} · {code}")

            try:
                local_res = recompute_locally(progress_cb=on_progress)
            except Exception as e:
                progress.empty()
                st.error(f"本地重算失败: {e}")
                st.stop()

            progress.progress(100, text="完成")
            time.sleep(0.3)
            progress.empty()

            if local_res["ok"] and local_res.get("n_etfs", 0) >= 50:
                final = {**local_res, "mode": "local",
                          "n_points": api_res.get("n_points", 0)}
                label = "✅ 本地 v27 重算完成"
            else:
                final = api_res
                label = "⚠️ 本地失败,回退 API 快照"

            st.session_state.refresh_state = final
            load_results.clear()
            load_history.clear()

            elapsed = int((time.time() - t_start) * 1000)
            st.markdown(
                f'<div style="background:{BG_PANEL};border:1px solid {ACCENT_DN};'
                f'border-radius:8px;padding:14px 18px;margin-top:8px;">'
                f'<div style="color:{ACCENT_DN};font-size:13px;font-weight:600;'
                f'margin-bottom:4px;">{label}</div>'
                f'<div style="color:{TEXT};font-size:12px;line-height:1.7;'
                f'font-family:monospace;">'
                f'· 数据日期(基于最新 K 线): <b>{final.get("asof_date", "—")}</b><br>'
                f'· 标的池: <b>{final["n_etfs"]}</b> 只 ETF<br>'
                f'· 趋势历史: <b>{api_res.get("n_points", 0)}</b> 天<br>'
                f'· 耗时: <b>{elapsed}</b>ms</div></div>',
                unsafe_allow_html=True,
            )
            st.rerun()
        elif rs:
            # 显示上次刷新状态
            fa = rs["fetched_at"].split("T")[1].split(".")[0] if "T" in rs["fetched_at"] else rs["fetched_at"]
            mode_label = "本地重算" if rs.get("mode") == "local" else "API"
            st.markdown(
                f'<div style="color:{TEXT_DIM};font-size:11px;padding-top:8px;'
                f'font-family:monospace;">'
                f'上次刷新: {fa} ({mode_label}) · {rs["n_etfs"]} 只 ETF'
                f' · {rs["elapsed_ms"]}ms'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.markdown(f'<div style="height:12px"></div>', unsafe_allow_html=True)

    # 路由所有 Tab
    # 各 Tab 内部决定是否要展示自己的 KPI(例如 ETF 强弱 Tab 会在内部显示标的池/趋势分布)
    render_all_tabs(df_res, df_hist)

    st.markdown(f"""
    <div style="margin-top:32px;padding-top:12px;border-top:1px solid {BORDER};
                color:{TEXT_DIM};font-size:10px;text-align:center;
                letter-spacing:0.5px;">
      🐑 羊羊股市监测 · 5min cache · AmazingData via CloudBase · 手动刷新请点上方按钮
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()
