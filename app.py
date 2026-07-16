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
# /healthz 健康检查 路由 (CloudBase 部署需要)
# Streamlit 默认只能在同一个 script 下接路由，但顶层用 _stcore/health 同样可用
# 这里提供一个自检便宜 · 如果文件 data 还没就绪不算失败
# ============================================================
import os as _os_health
_HEALTH_OK = _os_health.path.exists(str(Path(__file__).parent / "data"))
st.markdown(
    f'<!-- healthz={("ok" if _HEALTH_OK else "no_data")} -->',
    unsafe_allow_html=True,
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
    height: 42px;
    background: transparent;
    color: {TEXT_MUTED};
    border-radius: 6px 6px 0 0;
    font-weight: 500;
    padding: 0 16px;
    font-size: 13px;
    flex-shrink: 0;
    transition: background 0.15s, color 0.15s;
    border-bottom: 2px solid transparent;
  }}
  .stTabs [data-baseweb="tab"]:hover {{
    background: {BG_PANEL};
    color: {TEXT};
  }}
  .stTabs [aria-selected="true"] {{
    background: linear-gradient(180deg,{BG_PANEL_HI}66,{BG_PANEL});
    color: {TEXT};
    font-weight: 700;
    border-bottom: 2px solid {ACCENT_UP};
    box-shadow: inset 0 -1px 0 {ACCENT_UP}33;
  }}

  /* Radio / Segmented Control(子视图切换)— 字重加强 */
  [role="radiogroup"] [role="radio"] {{
    font-weight: 500 !important;
    transition: background 0.15s, border-color 0.15s;
  }}
  [role="radiogroup"] [role="radio"]:hover {{
    background: {BG_PANEL_HI} !important;
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

    # === Logo: SVG 几何羊脸 + 主标 题 ===
    logo_svg = (
        '<svg width="32" height="32" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg" '
        'style="flex-shrink:0;filter:drop-shadow(0 2px 4px rgba(0,0,0,0.4));">'
          # 渐变定义
          '<defs>'
            '<linearGradient id="logoGrad" x1="0%" y1="0%" x2="100%" y2="100%">'
              f'<stop offset="0%" stop-color="{ACCENT_UP}"/>'
              f'<stop offset="100%" stop-color="#ff8a3d"/>'
            '</linearGradient>'
          '</defs>'
          # 头部 圆
          '<circle cx="16" cy="17" r="10" fill="url(#logoGrad)" stroke="' + ACCENT_UP + '" stroke-width="0.8"/>'
          # 耳
          '<ellipse cx="7" cy="11" rx="3" ry="4.5" fill="#7a4a2a" transform="rotate(-25 7 11)"/>'
          '<ellipse cx="25" cy="11" rx="3" ry="4.5" fill="#7a4a2a" transform="rotate(25 25 11)"/>'
          # 腿 (4个)
          '<rect x="9" y="25" width="2.5" height="5" rx="1" fill="#5a3a1a"/>'
          '<rect x="14.5" y="25" width="2.5" height="5" rx="1" fill="#5a3a1a"/>'
          '<rect x="20" y="25" width="2.5" height="5" rx="1" fill="#5a3a1a"/>'
          # 脸
          '<ellipse cx="16" cy="18" rx="6" ry="5" fill="#f5e6d3"/>'
          # 眼
          '<circle cx="13" cy="17" r="1.2" fill="#1a1a1a"/>'
          '<circle cx="19" cy="17" r="1.2" fill="#1a1a1a"/>'
          '<circle cx="13.3" cy="16.6" r="0.3" fill="#fff"/>'
          '<circle cx="19.3" cy="16.6" r="0.3" fill="#fff"/>'
          # 嘴
          '<path d="M14 21 Q16 23 18 21" stroke="#1a1a1a" stroke-width="0.8" fill="none" stroke-linecap="round"/>'
          # 绵羊卷毛 top
          '<circle cx="11" cy="8" r="2.2" fill="#fff" opacity="0.85"/>'
          '<circle cx="16" cy="6.5" r="2.5" fill="#fff" opacity="0.85"/>'
          '<circle cx="21" cy="8" r="2.2" fill="#fff" opacity="0.85"/>'
        '</svg>'
    )

    header_html = (
        '<div style="display:flex;align-items:center;gap:12px;margin-bottom:2px;'
        f'padding-bottom:10px;border-bottom:1px solid {BORDER};">'
      '<div style="display:flex;align-items:center;gap:10px;">'
        + logo_svg +
        '<div style="display:flex;flex-direction:column;gap:1px;">'
          '<span style="margin:0;font-size:24px;font-weight:700;'
               f'letter-spacing:-0.4px;line-height:1.1;white-space:nowrap;'
               f'display:inline-flex;align-items:baseline;gap:6px;">'
          f'<span style="color:{TEXT};font-size:20px;line-height:1;">🐑</span>'
          f'<span style="background:linear-gradient(135deg,#fb923c 0%,#f43f5e 55%,#ec4899 100%);'
          f'-webkit-background-clip:text;background-clip:text;'
          f'-webkit-text-fill-color:transparent;color:transparent;">羊羊</span>'
          f'<span style="color:{TEXT};">股市监测</span>'
          '</span>'
          '<span style="color:{TEXT_DIM};font-size:12px;font-weight:400;margin-top:1px;">A股ETF·趋势分析</span>'
        '</div>'
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
            help="刷新数据 + 自动后台重算指标",
            use_container_width=True,
            type="primary",
            disabled=st.session_state._refreshing,
        )
    with info_col:
        rs = st.session_state.refresh_state
        if clicked:
            t_start = time.time()
            st.session_state._refreshing = True

            _today_str = time.strftime("%Y%m%d")
            _current_asof = (rs.get("asof_date") or "")[:8] if rs else ""
            _needs_network = (_current_asof != _today_str)

            # 持续显示进度条，用 placeholder 避免闪一下消失
            progress_ph = st.empty()
            bar = progress_ph.progress(0, text="拉取数据..." if _needs_network else "重读缓存...")
            try:
                if _needs_network:
                    bar.progress(20, text="API 拉取趋势分类...")
                    api_res = refresh_data()
                    if not api_res["ok"]:
                        progress_ph.empty()
                        st.error(f"拉取数据失败: {api_res['error']}")
                        st.session_state._refreshing = False
                        st.stop()
                    final = api_res
                    bar.progress(60, text="腾讯源补最新价...")
                    time.sleep(0.3)
                else:
                    bar.progress(30, text="重读缓存...")
                    load_results.clear()
                    load_history.clear()
                    df_res = load_results()
                    df_hist = load_history()
                    final = rs
                bar.progress(80, text="加载页面数据...")
                st.session_state.refresh_state = final
                load_results.clear()
                load_history.clear()
                df_res = load_results()
                df_hist = load_history()
                bar.progress(100, text="完成")
                time.sleep(0.3)
            except Exception as e:
                progress_ph.empty()
                st.error(f"异常: {e}")
                st.session_state._refreshing = False
                st.stop()

            elapsed = int((time.time() - t_start) * 1000)
            # 先清进度条，再渲染 header（按钮才恢复）
            render_header(df_res, final)
            progress_ph.empty()
            st.session_state._refreshing = False

            st.toast(
                "刷新完成: " + ("✅ 网络" if _needs_network else "✅ 缓存") +
                " · " + str(final.get('n_etfs', 0))
                + "只ETF · " + str(final.get('n_points', 0))
                + "天 · " + str(elapsed) + "ms",
                icon="📊",
            )

            if _needs_network:
                _spawn_recompute_background()

        _recompute_done_path = FETCH_DATA_DIR / ".recompute_done"
        if _recompute_done_path.exists():
            if not st.session_state.get("_recompute_loaded"):
                try:
                    load_results.clear()
                    load_history.clear()
                    df_res = load_results()
                    df_hist = load_history()
                    st.session_state["_recompute_loaded"] = True
                    _recompute_done_path.unlink(missing_ok=True)
                    st.rerun()
                except Exception:
                    pass
        else:
            st.session_state.pop("_recompute_loaded", None)

    st.markdown(f'<div style="height:12px"></div>', unsafe_allow_html=True)

    # 顶部小 KPI 实时状态条 (下次自动刷新倒计时 + 趋势最新 + 数据完整度)
    _render_status_strip(df_res, df_hist)

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


def _render_status_strip(df_res, df_hist):
    """顶部状态条: 距下次自动刷新 + 数据健康度 + 趋势最新日期"""
    now = time.localtime()
    next_run_min = 5
    cur_total_min = now.tm_hour * 60 + now.tm_min
    next_total_min = (now.tm_hour + 1) * 60 + next_run_min if cur_total_min >= next_run_min else now.tm_hour * 60 + next_run_min

    # 趋势最新日期 — 从 df_hist 列名取 (支持 d_YYYYMMDD 与 d_YYYY-MM-DD)
    last_date = "—"
    if not df_hist.empty:
        dates = []
        for c in df_hist.columns:
            if c.startswith("d_"):
                d = c[2:].replace("-", "").replace("/", "")
                if d.isdigit() and len(d) == 8:
                    dates.append(d)
        if dates:
            mx = max(dates)
            last_date = f"{mx[:4]}-{mx[4:6]}-{mx[6:]}"

    # 成交额为 0 的数量
    n_zero = 0
    n_total = 0
    if not df_res.empty and "latest_amount" in df_res.columns:
        n_total = len(df_res)
        n_zero = int((df_res["latest_amount"].fillna(0) <= 0).sum())

    # 本次刷新时间 — 优先从 session_state 读,否则从 CSV mtime 推断
    rs = st.session_state.get("refresh_state") or {}
    fetched_at = (rs.get("fetched_at") or "—")[:19].replace("T", " ")
    if fetched_at == "—":
        try:
            from pathlib import Path as _P
            results_p = _P(DATA_DIR) / "results.csv"
            if results_p.exists():
                mtime = results_p.stat().st_mtime
                fetched_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
        except Exception:
            pass

    # 倒计时秒数
    import datetime
    now_dt = datetime.datetime.now()
    next_hour = now_dt.hour + 1 if now_dt.minute >= 5 else now_dt.hour
    next_dt = now_dt.replace(hour=next_hour % 24, minute=5, second=0, microsecond=0)
    if next_hour >= 24:
        next_dt = next_dt + datetime.timedelta(days=1)
    remaining_sec = int((next_dt - now_dt).total_seconds())
    hh = remaining_sec // 3600
    mm = (remaining_sec % 3600) // 60
    ss = remaining_sec % 60
    countdown_str = f"{hh:02d}:{mm:02d}:{ss:02d}"

    countdown_ph = st.empty()
    countdown_ph.markdown(
        f'<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;'
        f'margin:4px 0 10px;padding:6px 12px;height:34px;'
        f'background:{BG_PANEL};border:1px solid {BORDER};border-radius:6px;'
        f'font-size:11px;color:{TEXT_MUTED};">'
        f'<span style="display:inline-flex;align-items:center;gap:4px;">'
        f'<span style="color:{TEXT_DIM};font-size:9px;text-transform:uppercase;letter-spacing:0.5px;">⏱ 下次刷新</span>'
        f'<span id="_cnt" style="color:{ACCENT_UP};font-family:monospace;font-weight:700;'
        f'font-size:11px;background:{ACCENT_UP}14;padding:0 6px;border-radius:3px;">{countdown_str}</span>'
        f'</span>'
        f'<span style="color:{BORDER_HI};">|</span>'
        f'<span style="display:inline-flex;align-items:center;gap:4px;">'
        f'<span style="color:{TEXT_DIM};font-size:9px;text-transform:uppercase;letter-spacing:0.5px;">📅 趋势</span>'
        f'<span style="color:{TEXT};font-family:monospace;font-weight:600;font-size:11px;">{last_date}</span>'
        f'</span>'
        f'<span style="color:{BORDER_HI};">|</span>'
        f'<span style="display:inline-flex;align-items:center;gap:4px;">'
        f'<span style="color:{TEXT_DIM};font-size:9px;text-transform:uppercase;letter-spacing:0.5px;">🔄 刷新</span>'
        f'<span style="color:{TEXT};font-family:monospace;font-weight:600;font-size:11px;">{fetched_at}</span>'
        f'</span>'
        f'<span style="color:{BORDER_HI};">|</span>'
        f'<span style="display:inline-flex;align-items:center;gap:4px;">'
        f'<span style="color:{TEXT_DIM};font-size:9px;text-transform:uppercase;letter-spacing:0.5px;">✓ 完整度</span>'
        f'<span style="color:{ACCENT_UP if n_zero == 0 else "#f59e0b"};font-family:monospace;font-weight:700;font-size:11px;'
        f'background:{(ACCENT_UP if n_zero == 0 else "#f59e0b")}14;padding:0 6px;border-radius:3px;">'
        f'{n_total - n_zero}/{n_total}</span>'
        f'</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    countdown_html = f"""
    <script>
    (function() {{
      var el = document.getElementById('_cnt');
      if (!el) return;
      var t = {remaining_sec};
      setInterval(function() {{
        t -= 1;
        if (t < 0) t = 3600;
        var h = Math.floor(t / 3600);
        var m = Math.floor((t % 3600) / 60);
        var s = t % 60;
        el.textContent = (h < 10 ? '0' : '') + h + ':' + (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
      }}, 1000);
    }})();
    </script>
    """
    import streamlit.components.v1 as components
    components.html(countdown_html, height=0)


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
