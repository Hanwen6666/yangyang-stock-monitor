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
    LABEL_ORDER,
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

@st.cache_data(ttl=300)
def load_results() -> pd.DataFrame:
    p = DATA_DIR / "results.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    # 兼容旧 CSV 缺少 latest_close/latest_volume 字段
    for col in ["latest_close", "latest_volume", "fund_size_yi", "latest_amount", "strength_label"]:
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
    n = len(df) if not df.empty else None

    # 上次刷新时间显示 —— 永久显示，刷新任何 widget 都不丢
    last_fetch_html = ""
    if refresh_state and refresh_state.get("fetched_at"):
        fa = refresh_state["fetched_at"]
        try:
            t = fa.split("T")[1].split(".")[0] if "T" in fa else fa.split(" ")[1]
            mode_label = "本地" if refresh_state.get("mode") == "local" else "API"
            n_etfs = refresh_state.get("n_etfs", 0)
            elapsed = refresh_state.get("elapsed_ms", 0)
            last_fetch_html = (
                f'<span style="color:{BORDER_HI};">|</span>'
                f'<span style="color:{TEXT_DIM};font-size:10px;">刷新</span>'
                f'<span style="color:{TEXT_MUTED};font-size:11px;font-family:monospace;">{t}</span>'
                f'<span style="color:{TEXT_DIM};font-size:9px;margin-left:2px;">'
                f'·{mode_label}·{n_etfs}只·{elapsed}ms</span>'
            )
        except Exception:
            last_fetch_html = f'<span style="color:{TEXT_DIM};font-size:10px;">刷新 {fa}</span>'

    header_html = (
        '<div style="display:flex;align-items:center;gap:12px;margin-bottom:2px;'
        f'padding-bottom:10px;border-bottom:1px solid {BORDER};">'
      '<div style="display:flex;align-items:baseline;gap:6px;">'
        '<span style="margin:0;font-size:18px;font-weight:700;'
             f'color:{TEXT};letter-spacing:-0.3px;">🐑 羊羊股市监测</span>'
        '<span style="color:{TEXT_DIM};font-size:11px;font-weight:400;">A股ETF·趋势分析</span>'
      '</div>'
      '<div style="margin-left:auto;display:flex;align-items:center;gap:6px;'
           f'background:{BG_PANEL};border:1px solid {BORDER};'
           'border-radius:6px;padding:4px 10px;">'
          f'<span style="color:{TEXT_DIM};font-size:10px;">数据</span>'
          f'<span style="color:{TEXT};font-size:11px;font-weight:600;font-family:monospace;">{asof_str}</span>'
          + (f'<span style="color:{BORDER_HI};">|</span>'
             f'<span style="color:{TEXT_DIM};font-size:10px;">池</span>'
             f'<span style="color:{ACCENT_UP};font-size:11px;font-weight:600;font-family:monospace;">{n}</span>'
             if n is not None else '')
          + last_fetch_html +
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

    # 诊断提示：如果表格里最新价/成交额列全为 0 · 说明加载的是 seed 旧数据
    _can_show_anomaly = (
        "latest_close" in df_res.columns and "latest_amount" in df_res.columns
    ) and (
        df_res["latest_amount"].fillna(0).gt(0).any()
    )

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

    # Fallback 后数据可能为 0(老 seed)——提示用户点刷新拉真数据
    if not is_empty and not _can_show_anomaly:
        st.info(
            "📦 当前是仓库内置的 **seed 数据快照**，最新价 / 成交额列均为 0。"
            "请点上方 **「🔄 数据刷新」** 拉取今日真实数据。",
            icon="⚠️",
        )
    if is_empty:
        # 首次启动 — 只显示提示,不自动调 API 防夸
        st.markdown(
            '<div style="background:#1a1f2e;border:1px solid #f59e0b;'
            'border-radius:8px;padding:20px;text-align:center;margin:24px 0;">'
            '<div style="color:#f59e0b;font-size:18px;font-weight:600;margin-bottom:8px;">'
            '📊 暂无数据</div>'
            '<div style="color:#9ca3af;font-size:13px;">'
            '点击下方 <b>「数据刷新」</b> 按钮,拉取最新数据(约 10 秒)'
            '</div></div>',
            unsafe_allow_html=True,
        )

    # 渲染 header
    render_header(df_res if not is_empty else pd.DataFrame(), st.session_state.refresh_state)

    # 单按钮: 先快速拉 API (4-6s) → 页面可用数据 → 后台异步跑 v27 重算
    if "_refreshing" not in st.session_state:
        st.session_state._refreshing = False
    btn_col, info_col = st.columns([1, 9])
    with btn_col:
        clicked = st.button(
            "🚧 刷新中..." if st.session_state._refreshing else "🔄 数据刷新",
            help="快速刷新 API 数据 (5s) + 异步重算 v27 (1-2 min, 后台不阻塞)",
            use_container_width=True,
            type="primary",
            disabled=st.session_state._refreshing,
        )
    with info_col:
        rs = st.session_state.refresh_state
        if clicked:
            t_start = time.time()
            st.session_state._refreshing = True
            progress = st.progress(0, text="拉取数据...")
            try:
                api_res = refresh_data()
                if not api_res["ok"]:
                    progress.empty()
                    st.error(f"拉取数据失败: {api_res['error']}")
                    st.session_state._refreshing = False
                    st.stop()
                final = api_res
                label = "✅ 快速刷新完成"
                progress.progress(100, text="完成")
                time.sleep(0.2)
                progress.empty()
                st.session_state.refresh_state = final
                load_results.clear()
                load_history.clear()
                df_res = load_results()
                df_hist = load_history()
            except Exception as e:
                progress.empty()
                st.error(f"异常: {e}")
                st.session_state._refreshing = False
                st.stop()

            elapsed = int((time.time() - t_start) * 1000)
            st.toast(
                "刷新完成: " + label + " · " + str(final.get('n_etfs', 0))
                + "只ETF · " + str(final.get('n_points', 0))
                + "天 · " + str(elapsed) + "ms",
                icon="📊",
            )
            render_header(df_res, final)
            st.session_state._refreshing = False

            # === 异步启动 v27 重算（后台跑，不阻塞主进程） ===
            _spawn_recompute_background()

        # 如果后台重算刚刚完成 → 自动加载新数据 + toast 提示
        _recompute_done_path = FETCH_DATA_DIR / ".recompute_done"
        if _recompute_done_path.exists():
            _toast_key = "_recompute_toasted"
            if not st.session_state.get(_toast_key):
                try:
                    load_results.clear()
                    load_history.clear()
                    df_res = load_results()
                    df_hist = load_history()
                    st.session_state[_toast_key] = True
                    st.toast("🧮 v27 重算数据已自动加载", icon="✅")
                except Exception:
                    pass
            # 标记清除（下次重算时新建）
            # 在 render 完之后清理，确保只 toast 一次
        else:
            st.session_state.pop("_recompute_toasted", None)
        # else: 「上次刷新」状态现在永久显示在 render_header 里了，不需要再这里重复

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


def _spawn_recompute_background():
    """后台线程启动 v27 重算（不阻塞 Streamlit script runner）

    重算结果写入 results.csv, 完成后标记 .recompute_done 文件。
    用户下次点刷新时自动加载重算后的数据。
    """
    import threading

    def _run():
        try:
            # 先清旧标记
            done_path = FETCH_DATA_DIR / ".recompute_done"
            if done_path.exists():
                done_path.unlink()
            from lib import algorithm as _algo
            # 跑重算
            res = recompute_locally()
            if res.get("ok") and res.get("n_etfs", 0) >= 50:
                from datetime import datetime as _dt
                done_path.write_text(
                    f"recompute_done ok n_etfs={res.get('n_etfs')} at {_dt.now().isoformat(timespec='seconds')}",
                    encoding="utf-8",
                )
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()


if __name__ == "__main__":
    main()
