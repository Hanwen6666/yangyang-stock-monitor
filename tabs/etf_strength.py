"""
ETF 强弱趋势分析 Tab

包含两个子视图:
  - 详细列表(全部 197 只 ETF 排序展示)
  - 趋势演变(25 天色块化热力图)

后续如果要把"详细列表"和"趋势演变"拆成两个顶层 Tab,改这里即可。
"""
import streamlit as st
import pandas as pd
from pathlib import Path


# 复用共享常量 + algorithm
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.constants import (  # noqa: E402
    BG_PANEL, BG_PANEL_HI, BORDER, BORDER_HI,
    TEXT, TEXT_MUTED, TEXT_DIM, ACCENT_UP, ACCENT_DN,
    LABEL_ORDER, LABEL_STYLES, SHORT_MAP,
    CHART_KLINE_HEIGHT,
    CACHE_TTL_ETF_LIST, CACHE_TTL_RECOMPUTE,  # 2026-07-20 重构: TTL 统一
)
from lib import algorithm as algo  # 仅 calc_single_etf 仍留 lib.algorithm
from lib import market_data as md  # 2026-07-21 E3 迁移: fetcher 迁到 lib.market_data
from lib.chart_kline import _kline_chart_html
from lib.ui_components import (
    label_badge_html, kpi_card, metric_row_html,
    fmt_code, fmt_name, fmt_chg_html, fmt_price,
    fmt_vol_yi, fmt_vol_simple, fmt_yi, fmt_category,
    height_spacer,  # 2026-07-21 δ 靶点
)  # 2026-07-20 重构: 9 个列 formatter 抽离


@st.cache_data(ttl=CACHE_TTL_RECOMPUTE, show_spinner=False)
def _cached_fetch_kline(code: str, min_len: int):
    """缓存 K 线数据(优先腾讯快照,更稳定),避免跨 tab 切换重复拉取卡死"""
    try:
        k = md.fetch_kline_tencent(code)
        if k is not None and len(k) >= 120:
            return k
    except Exception:
        pass
    k = md.fetch_kline(code, min_len)
    return k


@st.cache_data(ttl=CACHE_TTL_ETF_LIST, show_spinner=False)
def _cached_fetch_amount(code: str):
    """成交额缓存,避免每次进入个股分析重复拉取"""
    return md.fetch_amount(code)


@st.cache_data(ttl=CACHE_TTL_ETF_LIST, show_spinner=False)
def _prepare_list_view(df_res: pd.DataFrame, label_filter: str | None,
                        cat_filter_tuple=()):
    """缓存列表视图的筛选+排序,避免 segmented_control 切换时重复计算。

    cat_filter_tuple 为 tuple(不可变 list),以安全作为 cache key。
    """
    if label_filter is not None:
        df_view = df_res[df_res["strength_label"] == label_filter].copy()
    else:
        df_view = df_res.copy()
    if cat_filter_tuple:
        df_view = df_view[df_view["category"].isin(list(cat_filter_tuple))]
    df_view = sort_df(df_view, "strength_label", "desc")
    return df_view


@st.cache_data(ttl=CACHE_TTL_ETF_LIST, show_spinner=False)
def _build_history_html(df_hist: pd.DataFrame, points_tuple, df_res: pd.DataFrame,
                         selected_dates_tuple, label_filter_list):
    """缓存趋势演变 HTML,避免 radio 切换时重复构建 207×N 个色块。
    直接以 DataFrame 为入参 -- 当数据真的变了会自然重新计算。"""
    points = list(points_tuple)
    selected_dates = list(selected_dates_tuple)

    df = df_hist.copy()
    if label_filter_list:
        latest_point = points[-1]
        df = df[df[latest_point].isin(label_filter_list)]

    # 日期格式
    col_rename = {}
    for p in selected_dates:
        if "_" in p:
            raw = p.split("_")[1][:10]
            col_rename[p] = raw[5:10] if len(raw) >= 10 else raw
        else:
            col_rename[p] = p

    show = df[["code", "name"] + selected_dates].copy()
    show = show.rename(columns={"code": "代码", "name": "名称"})
    show = show.rename(columns=col_rename)
    date_cols = [c for c in col_rename.values() if c in show.columns]

    for p, disp in col_rename.items():
        if disp in show.columns:
            show[disp] = show[disp].apply(_compact_cell_html)

    base_cols = ["代码", "名称"]
    show = show[base_cols + date_cols]

    html_body = show.to_html(escape=False, index=False, border=0, classes="compact-table")
    return html_body, len(df), len(date_cols)


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


# 2026-07-20 重构: 9 个列 formatter 抽到 lib/ui_components, 加列只改 dict (OCP)
_COLUMN_FORMATS = {
    "代码": fmt_code,
    "名称": fmt_name,
    "涨跌幅": fmt_chg_html,
    "最新价": fmt_price,
    "成交额(亿)": fmt_vol_yi,
    "规模(亿)": fmt_yi,
    "分类": fmt_category,
}
_TABLE_COLS_ORDER = [
    "code", "name", "strength_label", "change_pct", "category",
    "latest_close", "latest_amount", "fund_size_yi",
]
_TABLE_COL_RENAME = {
    "code": "代码", "name": "名称", "strength_label": "趋势",
    "change_pct": "涨跌幅",
    "category": "分类",
    "latest_close": "最新价", "latest_amount": "成交额(亿)",
    "fund_size_yi": "规模(亿)",
}


def _prepare_table_data(df: pd.DataFrame) -> pd.DataFrame:
    """2026-07-21 ζ 靶点: 抽数据 select + rename + formatter 应用 (约 20L)

    纯数据处理层,无 streamlit 依赖,可单测。
    """
    show = df[[c for c in _TABLE_COLS_ORDER if c in df.columns]].copy()
    show = show.rename(columns=_TABLE_COL_RENAME)
    if "趋势" in show.columns:
        show["趋势"] = show["趋势"].apply(label_badge_html)
    for col_name, fmt_fn in _COLUMN_FORMATS.items():
        if col_name in show.columns:
            show[col_name] = show[col_name].apply(fmt_fn)
    return show


def _render_etf_table_html(show: pd.DataFrame, uid: int) -> None:
    """2026-07-21 ζ 靶点: 渲染表格 HTML + 过滤 input + 计数 (约 20L)

    视图层第一步:展示搜索框 + 表格本体 + 客户端过滤的 input 元素。
    """
    st.markdown(
        f'<div style="color:{TEXT_DIM};font-size:10px;margin-bottom:4px;'
        f'display:flex;align-items:center;gap:8px;">'
        f'<span>共 <b style="color:{TEXT};">{len(show)}</b> 只 · 点击表头排序 · 输关键词过滤</span>'
        f'<input type="search" id="etf_search_{uid}" placeholder="🔍 过滤代码/名称/趋势" '
        f'style="margin-left:auto;background:{BG_PANEL};color:{TEXT};border:1px solid {BORDER};'
        f'border-radius:4px;padding:2px 8px;font-size:11px;width:240px;outline:none;" '
        f'autocomplete="off"/>'
        f'<span id="etf_search_count_{uid}" style="color:{TEXT_DIM};font-size:10px;"></span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="etf-table-wrap" data-uid="{uid}">'
        f'{show.to_html(escape=False, index=False, border=0, classes="etf-table")}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_etf_table_interactivity(uid: int) -> None:
    """2026-07-21 ζ 靶点: 渲染客户端 JS 排序/过滤脚本 + CSS 样式 (约 200L)

    视图层第二步:把过滤/排序交互逻辑下沉到客户端,避免后端 round-trip。
    """
    st.markdown(f"""
    <script>
    (function(){{
      var uid = '{uid}';
      var input = document.getElementById('etf_search_' + uid);
      var counter = document.getElementById('etf_search_count_' + uid);
      var wrap = document.querySelector('.etf-table-wrap[data-uid="' + uid + '"]');
      if (!input || !wrap) return;
      var rows = wrap.querySelectorAll('tbody tr');
      var total = rows.length;
      function apply(){{
        var q = (input.value || '').trim().toLowerCase();
        var visible = 0;
        rows.forEach(function(r){{
          if (!q) {{ r.style.display = ''; visible++; return; }}
          var t = r.textContent.toLowerCase();
          r.style.display = t.indexOf(q) >= 0 ? '' : 'none';
          if (r.style.display !== 'none') visible++;
        }});
        if (counter) counter.textContent = q ? (visible + ' / ' + total) : '';
      }}
      input.addEventListener('input', apply);
      input.addEventListener('keydown', function(e){{
        if (e.key === 'Escape') {{ input.value = ''; apply(); }}
      }});

      var ths = wrap.querySelectorAll('thead th');
      ths.forEach(function(th, idx){{
        if (!th.dataset.label) th.dataset.label = th.textContent.trim();
        th.dataset.colIdx = idx;
        th.addEventListener('click', function(){{
          var dir;
          if (th.classList.contains('sort-asc')) {{
            dir = 'desc'; th.classList.remove('sort-asc'); th.classList.add('sort-desc');
          }} else if (th.classList.contains('sort-desc')) {{
            th.classList.remove('sort-desc');
            th.textContent = th.dataset.label; dir = null;
          }} else {{
            ths.forEach(function(o){{
              if (o !== th) {{ o.classList.remove('sort-asc'); o.classList.remove('sort-desc'); }}
            }});
            th.classList.add('sort-asc'); dir = 'asc';
          }}
          if (dir) th.textContent = th.dataset.label; else th.textContent = th.dataset.label;
          var tbody = wrap.querySelector('tbody');
          var rowsArr = Array.prototype.slice.call(rows);
          if (dir) {{
            rowsArr.sort(function(a,b){{
              var av = a.children[idx].textContent.trim();
              var bv = b.children[idx].textContent.trim();
              var an = parseFloat(av.replace(/[^\\d.\\-]/g, ''));
              var bn = parseFloat(bv.replace(/[^\\d.\\-]/g, ''));
              var isNum = !isNaN(an) && !isNaN(bn) && /[\\d.]/.test(av) && /[\\d.]/.test(bv);
              if (isNum) return dir === 'asc' ? an - bn : bn - an;
              return dir === 'asc' ? av.localeCompare(bv, 'zh-CN') : bv.localeCompare(av, 'zh-CN');
            }});
            rowsArr.forEach(function(r){{ tbody.appendChild(r); }});
          }} else {{
            rowsArr.sort(function(a,b){{
              return (parseInt(a.dataset.origIdx||0) - parseInt(b.dataset.origIdx||0));
            }});
            rowsArr.forEach(function(r){{ tbody.appendChild(r); }});
          }}
          apply();
        }});
      }});
      rows.forEach(function(r,i){{ r.dataset.origIdx = i; }});
    }})();
    </script>
    """, unsafe_allow_html=True)
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
        text-align: left; padding: 5px 8px;
        border-bottom: 1px solid {BORDER}; text-transform: uppercase;
        letter-spacing: 0.8px; white-space: nowrap;
        position: sticky; top: 0; z-index: 1;
        cursor: pointer; user-select: none;
        transition: background 0.15s, color 0.15s;
      }}
      .etf-table th:hover {{ background: {BORDER}; color: {TEXT}; }}
      .etf-table th.sort-asc::after  {{ content: ' ▲'; color: {ACCENT_UP}; }}
      .etf-table th.sort-desc::after {{ content: ' ▼'; color: {ACCENT_DN}; }}
      .etf-table th:nth-child(2),
      .etf-table th:nth-child(3) {{ min-width: 80px; }}
      .etf-table th:nth-child(3),
      .etf-table td:nth-child(3) {{ min-width: 84px; }}
      .etf-table th:nth-child(5),
      .etf-table td:nth-child(5) {{ min-width: 72px; }}
      .etf-table th:last-child {{ text-align: right; }}
      .etf-table td {{
        padding: 7px 8px; border-bottom: 1px solid #151b2a;
        color: {TEXT}; white-space: nowrap; font-feature-settings: "tnum";
        font-size: 12px;
      }}
      .etf-table td:nth-child(2) {{ max-width: 220px; overflow: hidden; text-overflow: ellipsis; }}
      .etf-table tr {{
        transition: all 0.18s cubic-bezier(0.4,0,0.2,1) !important;
        position: relative;
      }}
      .etf-table tr:hover td {{
        background: linear-gradient(90deg, rgba(91,147,224,0.10), rgba(91,147,224,0.04)) !important;
        color: #fff !important;
      }}
      .etf-table tr:hover td:first-child {{
        box-shadow: inset 3px 0 0 #5b93e0 !important;
        background: linear-gradient(90deg, rgba(91,147,224,0.18), rgba(91,147,224,0.10)) !important;
        color: #5b93e0 !important;
        font-weight: 700;
      }}
      .etf-table tr:hover td:nth-child(4) {{
        filter: brightness(1.3);
      }}
      .etf-table tr:hover td:nth-child(2)::after {{
        content: ' › 查看详情';
        color: #5b93e0;
        font-size: 10px;
        font-weight: 600;
        margin-left: 6px;
        opacity: 0.8;
      }}
      .etf-table tr:last-child td {{ border-bottom: none; }}
      .back-to-top {{
        position:fixed;bottom:60px;right:24px;z-index:1000;
        width:38px;height:38px;border-radius:50%;
        background:linear-gradient(135deg,#1e2537,#181e2e);
        border:1px solid {BORDER_HI};
        color:{TEXT_DIM};font-size:18px;
        cursor:pointer;display:flex;align-items:center;justify-content:center;
        box-shadow:0 4px 12px rgba(0,0,0,0.4);
        transition:all 0.2s ease;
      }}
      .back-to-top:hover {{
        background:{BG_PANEL_HI};border-color:{TEXT_DIM};
        color:{TEXT};transform:translateY(-2px);
      }}
      @keyframes skeleton-shimmer {{
        0%   {{ background-position: -400px 0; }}
        100% {{ background-position: 400px 0; }}
      }}
      .skeleton-bar {{
        background: linear-gradient(90deg,
          #1a1f2e 0%, #252b3e 50%, #1a1f2e 100%);
        background-size: 400px 100%;
        animation: skeleton-shimmer 1.6s ease-in-out infinite;
        border-radius: 4px;
        height: 14px;
        margin: 6px 0;
      }}
      .skeleton-bar.tall {{ height: 22px; }}
      .skeleton-bar.short {{ width: 60%; }}
      .skeleton-bar.medium {{ width: 80%; }}
    </style>
    """, unsafe_allow_html=True)


def render_table(df: pd.DataFrame):
    """ETF 列表表格 (2026-07-21 ζ 靶点: 234L → 调度器 + 3 个 _xxx 子函数)

    三段式:数据准备 → HTML 渲染 → 交互脚本/CSS
    """
    if df.empty:
        st.info("无匹配数据")
        return

    show = _prepare_table_data(df)
    uid = id(df)
    _render_etf_table_html(show, uid)
    _render_etf_table_interactivity(uid)


def render_history_table(df_hist: pd.DataFrame, df_res: pd.DataFrame):
    """趋势演变子视图 - 紧凑色块矩阵
    列: 代码 / 名称 / [日期色块] (最新→最远)

    简化:去掉筛选区,默认最近 7 天,展示全部 207 只
    """
    if df_hist.empty or df_res.empty:
        st.info("暂无趋势历史数据")
        return

    points = [c for c in df_hist.columns if c not in ("code", "name")]
    if not points:
        st.info("无趋势数据点")
        return

    # 默认取最近 7 天(点从最新→最远)
    default_n = 25
    date_options_rev = list(reversed(points))
    selected_points = date_options_rev[:min(default_n, len(date_options_rev))]

    # [FIX] Bug #1 - pool size mismatch
    # etf_trend_history.csv contains 207 (incl delisted/suspended ETFs),
    # but dashboard KPI shows df_res pool (results.csv = 197)
    # Filter by df_res.code so history tab matches dashboard
    valid_codes = set(df_res["code"].astype(str).str.zfill(6).tolist())
    df = df_hist[df_hist["code"].astype(str).str.zfill(6).isin(valid_codes)].copy()

    # === Build display (cached) ===
    _pts_tuple = tuple(points)
    _sel_dates_tuple = tuple(selected_points)
    _lbl_filter_list = []

    import time
    t0 = time.time()
    with st.spinner(f"build trend matrix {len(df)}x{len(selected_points)}..."):
        html_body, n_etf, n_days = _build_history_html(
            df, _pts_tuple, df_res, _sel_dates_tuple, _lbl_filter_list
        )
    render_ms = int((time.time() - t0) * 1000)

    st.markdown(
        f'<div style="color:{TEXT_DIM};font-size:11px;margin-bottom:6px;'
        f'display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;">'
        f'<span style="font-weight:500;color:{TEXT};">共 {n_etf} 只 · {n_days} 天 '
        f'<span style="color:{TEXT_DIM};font-size:10px;margin-left:6px;">'
        f'拼装 {render_ms}ms</span></span>'
        f'<span style="font-size:11px;letter-spacing:0.3px;display:flex;align-items:center;gap:6px;">'
        f'<span style="color:{TEXT_DIM};font-size:10px;text-transform:uppercase;">图例</span>'
        f'{label_badge_html("超强势")} '
        f'{label_badge_html("强势")} '
        f'{label_badge_html("震荡上涨")} '
        f'{label_badge_html("横盘震荡")} '
        f'{label_badge_html("震荡下跌")} '
        f'{label_badge_html("一直下跌")}'
        f'</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="compact-table-wrap">'
        f'{html_body}'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown(f"""
    <style>
      .compact-table-wrap {{
        max-height: 520px; overflow-y: auto; overflow-x: auto;
        border-radius: 8px; border: 1px solid {BORDER};
        background: linear-gradient(180deg,rgba(19,24,38,0.98),rgba(10,14,26,0.98));
        box-shadow:0 2px 12px rgba(0,0,0,0.3);
      }}
      .compact-table-wrap::-webkit-scrollbar {{ width: 6px; height: 6px; }}
      .compact-table-wrap::-webkit-scrollbar-track {{ background: transparent; }}
      .compact-table-wrap::-webkit-scrollbar-thumb {{ background: {BORDER_HI}; border-radius: 3px; }}
      .compact-table-wrap::-webkit-scrollbar-thumb:hover {{ background: #3a4a6a; }}
      .compact-table {{ border-collapse: collapse; table-layout: fixed; }}
      .compact-table thead {{ position: sticky; top: 0; z-index: 2; }}
      .compact-table th {{
        background: linear-gradient(180deg,#1e2537,#181e2e);
        color: {TEXT_MUTED};
        font-weight: 600; font-size: 10px;
        text-align: center !important; padding: 4px 1px;
        border-bottom: 1px solid {BORDER};
        text-transform: uppercase; letter-spacing: 0.4px;
        white-space: nowrap;
      }}
      .compact-table th:nth-child(-n+2) {{ text-align: left !important; padding-left: 6px; padding-right: 0 !important; }}
      .compact-table td {{
        padding: 5px 1px; border-bottom: 1px solid rgba(31,38,56,0.4);
        color: {TEXT}; white-space: nowrap;
        font-size: 11px; text-align: center;
        line-height: 1.5;
        overflow: hidden; text-overflow: ellipsis;
        height: 30px;
      }}
      .compact-table td:nth-child(-n+2) {{ text-align: left !important; padding-left: 6px; padding-right: 0 !important; font-size: 11px; }}
      .compact-table th:first-child, .compact-table td:first-child {{
        width: 64px !important;
        padding-right: 4px !important;
      }}
      .compact-table th:first-child {{ background: linear-gradient(180deg,#1e2537,#181e2e); }}
      .compact-table td:first-child {{
        background: rgba(10,14,26,0.85);
        font-family: 'SF Mono','Fira Code','Consolas',monospace;
        letter-spacing: 0.2px;
      }}
      .compact-table th:nth-child(2),
      .compact-table td:nth-child(2) {{
        width: 140px !important;
        padding-left: 2px !important;
      }}
      /* 日期列宽度统一 */
      .compact-table th:nth-child(n+3),
      .compact-table td:nth-child(n+3) {{
        width: 42px !important;
        padding: 5px 2px !important;
      }}
      .compact-table th {{ padding: 6px 1px !important; }}
      .compact-table tr:hover td:first-child {{ background: rgba(26,32,48,0.95); }}
      .compact-table tr:hover td {{ background: rgba(20,24,40,0.6); }}
      .compact-table tr:last-child td {{ border-bottom: none; }}
    </style>
    """, unsafe_allow_html=True)


def _compact_cell_html(label: str) -> str:
    """趋势色块 - 大厂质感(渐变底 + 圆角 + 微光晕 + hover title)"""
    if not label or pd.isna(label) or label == "":
        return '<span style="color:#54586b">-</span>'
    s = LABEL_STYLES.get(label)
    if not s:
        return f'<span title="{label}">{label}</span>'
    return (
        f'<span title="{label}" style="'
        f'background:{s["gradient"]};color:{s["fg"]};'
        f'padding:1px 2px;border-radius:3px;'
        f'font-size:11px;font-weight:600;letter-spacing:0.1px;'
        f'display:inline-block;text-align:center;'
        f'white-space:nowrap;line-height:1.55;'
        f'box-shadow:inset 0 1px 0 rgba(255,255,255,0.15),0 1px 2px rgba(0,0,0,0.3);'
        f'transition:transform 0.15s ease,box-shadow 0.15s ease;'
        f'cursor:default;">{label}</span>'
    )


def render_list_view(df_res: pd.DataFrame, label_filter: str | None = None):
    """详细列表子视图 - 含行业分类快速过滤

    布局:
      [全行业 multiselect] [共 X 只 · label]         [排序首位 → 右侧]
      ─── 表格 ───
    """
    # 行业过滤:仅在有 category 列时启用
    cats = sorted(df_res["category"].dropna().astype(str).unique().tolist()) \
        if "category" in df_res.columns else []
    cat_filter: list[str] = []

    df_view = _prepare_list_view(df_res, label_filter, tuple(cat_filter))

    # === 单行布局: 全行业 · 共 X 只 · 排序首位 ===
    # 比例: 行业筛选 4 · 标的数 2 · 排序首位 4
    if cats:
        col_filter, col_count, col_top = st.columns([4, 2, 4], gap="small")
    else:
        col_count, col_top = st.columns([2, 4], gap="small")
        col_filter = None

    if col_filter is not None:
        with col_filter:
            cat_filter = st.multiselect(
                "全行业", cats, default=[],
                placeholder="🔍 全部行业",
                label_visibility="collapsed",
                key=f"cat_filter_{label_filter or 'all'}",
                help="多选筛行业;不选 = 全部",
            )

    # 若有筛选,重算 df_view
    if cat_filter:
        df_view = _prepare_list_view(df_res, label_filter, tuple(cat_filter))

    # 计算要展示的"标的数"标签
    label_tag_html = ""
    if label_filter:
        s = LABEL_STYLES.get(label_filter)
        glow = s["glow"] if s else ACCENT_UP
        label_tag_html = (
            f'<span style="background:{glow}22;color:{glow};'
            f'padding:1px 6px;border-radius:4px;font-size:10px;'
            f'font-weight:600;margin-left:4px;">{label_filter}</span>'
        )
    industry_tag_html = ""
    if cat_filter:
        industry_tag_html = (
            f'<span style="background:{BG_PANEL_HI};color:{TEXT};'
            f'padding:1px 6px;border-radius:4px;font-size:10px;'
            f'font-weight:600;margin-left:4px;">'
            f'{len(cat_filter)}个行业</span>'
        )

    with col_count:
        st.markdown(
            f'<div style="display:flex;align-items:center;height:40px;'
            f'padding:0 10px;background:{BG_PANEL};border:1px solid {BORDER};'
            f'border-radius:6px;gap:6px;flex-wrap:wrap;">'
            f'<span style="color:{TEXT_DIM};font-size:10px;font-weight:500;'
            f'text-transform:uppercase;letter-spacing:0.5px;">共</span>'
            f'<span style="color:{ACCENT_UP};font-size:18px;font-weight:700;'
            f'font-family:monospace;line-height:1;">{len(df_view):,}</span>'
            f'<span style="color:{TEXT_MUTED};font-size:11px;">只</span>'
            + label_tag_html + industry_tag_html +
            f'</div>',
            unsafe_allow_html=True,
        )

    with col_top:
        top = df_view.iloc[0] if len(df_view) else None
        if top is not None:
            s = LABEL_STYLES.get(top["strength_label"])
            glow = s["glow"] if s else TEXT_MUTED
            bg = s["bg"] if s else BORDER_HI
            fg = s["fg"] if s else TEXT
            # 名称超 8 字时截断(防止挤压)
            name_disp = top["name"]
            if len(name_disp) > 8:
                name_disp = name_disp[:7] + "…"
            # 三段式布局: [排序首位 label] [代码 + 名称] [趋势 chip]
            # 趋势 chip 独立 flex-shrink:0 不被挤压
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:6px;height:40px;">'
                # 趋势 chip 靠右
                f'<span style="flex-shrink:0;background:{bg};color:{fg};'
                f'padding:3px 8px;border-radius:4px;font-size:10px;'
                f'font-weight:600;line-height:1.2;">{top["strength_label"]}</span>'
                # 代码 + 名称 (剩余空间)
                f'<span style="flex:1;min-width:0;display:flex;align-items:center;'
                f'justify-content:flex-end;gap:6px;background:{BG_PANEL};border:1px solid {BORDER};'
                f'border-radius:6px;padding:0 10px;height:100%;overflow:hidden;">'
                f'<span style="color:{TEXT_DIM};font-size:10px;flex-shrink:0;">排序首位</span>'
                f'<span style="color:{TEXT};font-size:13px;font-weight:600;'
                f'font-family:monospace;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'
                f'flex-shrink:1;min-width:0;">{top["code"]} {name_disp}</span>'
                f'</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="height:40px;"></div>',
                unsafe_allow_html=True,
            )

    title_extra = f" · {label_filter}" if label_filter else ""
    if cat_filter:
        title_extra += f" · {len(cat_filter)}个行业"

    st.markdown(height_spacer(6), unsafe_allow_html=True)
    if df_view.empty:
        st.info(f"该分类下暂无 ETF{title_extra}")
    else:
        render_table(df_view)


def _render_anomaly_banner(df_res: pd.DataFrame, df_hist: pd.DataFrame):
    """KPI 卡下面一行「🚨 板块异动」横幅 + 「⬇️ 导出 CSV」按钮

    业务上接 6 档 Δpp，今天最强的资金动向和最狠的流出用一句换一句话讲完。
    """
    total_size = df_res["fund_size_yi"].sum() if "fund_size_yi" in df_res.columns else 0
    today = {}
    for label in LABEL_ORDER:
        sub = df_res[df_res["strength_label"] == label]
        today[label] = sub["fund_size_yi"].sum() if "fund_size_yi" in sub.columns else 0

    # 昨日同一档位资金占比 — 统一分母(只用 history 子集,保证 today/prev 可比)
    prev_size_by_label = {}
    if df_hist is not None and not df_hist.empty:
        try:
            points = [c for c in df_hist.columns if c not in ("code", "name")]
            if len(points) >= 2:
                prev_point = points[-2]
                size_map = dict(zip(df_res["code"].astype(str), df_res["fund_size_yi"]))
                for lbl in LABEL_ORDER:
                    cnt_df = df_hist[df_hist[prev_point] == lbl]
                    s = sum(float(size_map.get(str(r["code"]), 0) or 0) for _, r in cnt_df.iterrows())
                    prev_size_by_label[lbl] = s
        except Exception:
            prev_size_by_label = {}

    # 统一分母:历史子集的总资金(若昨日没数据,退回到全 df_res 总规模)
    prev_total = sum(prev_size_by_label.values())
    cur_denom = prev_total if prev_total > 0 else total_size

    diffs = []
    for label in LABEL_ORDER:
        cur_pct = (today[label] / cur_denom * 100) if cur_denom else 0
        prev_pct = (prev_size_by_label.get(label, 0) / cur_denom * 100) if cur_denom else 0
        pp = cur_pct - prev_pct
        if pp != 0:
            diffs.append((label, pp))
    diffs.sort(key=lambda x: x[1])
    if not diffs:
        return
    top_in = diffs[-1]   # 净流入最多
    top_out = diffs[0]   # 净流出最多

    def _chip(label, pp, direction: str):
        """方向驱动的 chip:in=流入(红暖),out=流出(绿冷)

        不再用趋势档位 glow 色做底色,改用流入/流出语义色 —— 扫一眼就知道方向。
        """
        is_in = direction == "in"
        accent = ACCENT_UP if is_in else ACCENT_DN    # 红=流入 / 绿=流出
        arrow = "▲" if is_in else "▼"
        sign = "+" if pp > 0 else ""
        ls = LABEL_STYLES.get(label)
        tag_bg = ls["bg"] if ls else BORDER_HI
        tag_fg = ls["fg"] if ls else TEXT_MUTED
        return (
            f'<span style="display:inline-flex;align-items:center;gap:5px;'
            f'background:{accent}1a;color:{accent};'
            f'padding:2px 8px;border-radius:4px;'
            f'font-size:11px;font-weight:700;margin-right:6px;'
            f'border:1px solid {accent}33;">'
            f'<span style="font-size:11px;line-height:1;">{arrow}</span>'
            f'<span>{sign}{pp:.1f}pp</span>'
            f'<span style="background:{tag_bg};color:{tag_fg};'
            f'padding:0 5px;border-radius:3px;font-size:9px;'
            f'font-weight:600;letter-spacing:0.3px;">{label}</span>'
            f'</span>'
        )

    # 新设计: 左红右绿 · 中间分隔线 · 顶部标签 / 底部 chip
    # 扫读一眼就知道哪个方向强
    in_arrow = "▲"
    out_arrow = "▼"
    banner_html = (
        f'<div style="display:flex;align-items:stretch;gap:0;margin-top:8px;'
        f'border:1px solid {BORDER};border-radius:6px;overflow:hidden;'
        f'background:{BG_PANEL};">'
        # 左:资金流入 (红色调)
        f'<div style="flex:1;padding:10px 14px;'
        f'background:linear-gradient(90deg,rgba(255,77,79,0.10),rgba(255,77,79,0.02));'
        f'border-right:1px solid {BORDER};">'
        f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">'
        f'<span style="color:{ACCENT_UP};font-size:13px;font-weight:700;">{in_arrow} 涨幅领跑</span>'
        f'<span style="color:{TEXT_DIM};font-size:9px;text-transform:uppercase;'
        f'letter-spacing:0.5px;margin-left:auto;">relative +pp</span>'
        f'</div>'
        f'{_chip(top_in[0], top_in[1], "in")}'
        f'</div>'
        # 右:相对弱势 (绿色调)
        f'<div style="flex:1;padding:10px 14px;'
        f'background:linear-gradient(90deg,rgba(34,197,94,0.02),rgba(34,197,94,0.10));">'
        f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">'
        f'<span style="color:{ACCENT_DN};font-size:13px;font-weight:700;">{out_arrow} 涨幅垫底</span>'
        f'<span style="color:{TEXT_DIM};font-size:9px;text-transform:uppercase;'
        f'letter-spacing:0.5px;margin-left:auto;">relative -pp</span>'
        f'</div>'
        f'{_chip(top_out[0], top_out[1], "out")}'
        f'</div>'
        f'</div>'
    )

    st.markdown(banner_html, unsafe_allow_html=True)


def render_kpi(df: pd.DataFrame, df_hist: pd.DataFrame | None = None):
    """ETF 强弱趋势 Tab 专属 KPI:标的池 + 6 档趋势分布

    每个 KPI 卡 sub字段同时给出:
      - 只数 + 数量占比
      - 资金(亿) + 资金占比 + 与昨日同一档位对比的 Δ 箭头
    df_hist: 趋势演变历史表,用于算昨日同一档位的资金占比 diff.
             为 None 时不显示 diff。
    """
    total_size = df["fund_size_yi"].sum() if "fund_size_yi" in df.columns else 0
    n_total = len(df)

    # 昨日同一档位资金占比:从 history CSV 取最后 2 个日期点,只对比同一批 ETF 的趋势历史
    prev_size_by_label = {}
    if df_hist is not None and not df_hist.empty and total_size > 0:
        try:
            points = [c for c in df_hist.columns if c not in ("code", "name")]
            if len(points) >= 2:
                prev_point = points[-2]  # 倒数第二列 = 昨日
                prev_counts = df_hist[prev_point].value_counts().to_dict()
                # 按代码映射到 fund_size
                size_map = dict(zip(df["code"].astype(str), df["fund_size_yi"]))
                for lbl, cnt in prev_counts.items():
                    prev_sizes = 0.0
                    for _, r in df_hist[df_hist[prev_point] == lbl].iterrows():
                        prev_sizes += float(size_map.get(str(r["code"]), 0) or 0)
                    prev_size_by_label[lbl] = prev_sizes
        except Exception:
            prev_size_by_label = {}

    # 注入 KPI 样式（Step2: 3D悬浮升级 + 涨跌色脉冲动画）
    st.markdown(f"""
    <style>
      .kpi-card {{
        transition: all 0.25s cubic-bezier(0.4,0,0.2,1) !important;
        box-shadow: 0 1px 3px rgba(0,0,0,0.2);
      }}
      /* 有鼠标时hover — 3D悬浮效果 */
      @media (hover: hover) {{
        .kpi-card:hover {{
          background:{BG_PANEL_HI} !important;
          border-color:{BORDER_HI} !important;
          transform: translateY(-4px) scale(1.02);
          box-shadow:
            0 12px 28px rgba(0,0,0,0.5),
            0 4px 8px rgba(0,0,0,0.3),
            inset 0 1px 0 rgba(255,255,255,0.08) !important;
        }}
        .kpi-card:hover .kpi-value {{
          filter: brightness(1.25);
          text-shadow: 0 0 12px currentColor;
        }}
        .kpi-card:hover > div:first-child {{
          /* 顶部色带加亮 */
          filter: brightness(1.3);
        }}
      }}
      @media (hover: none) {{
        .kpi-card:active {{
          background:{BG_PANEL_HI} !important;
          border-color:{BORDER_HI} !important;
          transform: scale(0.97);
          transition: all 0.1s ease !important;
        }}
      }}
      /* 涨跌色脉冲 — 极端档位(超强势/一直下跌)数字脉动 */
      @keyframes kpi-pulse-up {{
        0%, 100% {{ text-shadow: 0 0 4px rgba(255,77,79,0.4); }}
        50%      {{ text-shadow: 0 0 10px rgba(255,77,79,0.85), 0 0 18px rgba(255,77,79,0.3); }}
      }}
      @keyframes kpi-pulse-down {{
        0%, 100% {{ text-shadow: 0 0 4px rgba(34,197,94,0.4); }}
        50%      {{ text-shadow: 0 0 10px rgba(34,197,94,0.85), 0 0 18px rgba(34,197,94,0.3); }}
      }}
      /* KPI 列间距+7列flex均匀分布 */
      .row-widget.stHorizontal {{ gap: 8px !important; }}
      .row-widget.stHorizontal > div {{ flex: 1 1 0 !important; min-width: 0 !important; }}
      /* radio 横向滚动(防换行) */
      div[data-testid="stRadio"] > div {{
        display: flex !important; flex-wrap: nowrap !important;
        overflow-x: auto !important; gap: 2px !important;
        padding-bottom: 4px; scrollbar-width: thin;
      }}
      div[data-testid="stRadio"] label {{
        flex: 0 0 auto !important; white-space: nowrap !important;
        padding: 4px 10px !important; font-size: 12px !important;
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
    # 6 档趋势 - sub 使用 sub_html(资金占比突出 + Δ diff)
    for i, label in enumerate(LABEL_ORDER, start=1):
        sub_df = df[df["strength_label"] == label]
        count = len(sub_df)
        size = sub_df["fund_size_yi"].sum() if "fund_size_yi" in sub_df.columns else 0
        pct_count = count / n_total * 100 if n_total > 0 else 0
        pct_size = (size / total_size * 100) if total_size > 0 else 0
        ls = LABEL_STYLES.get(label)

        # Δ 资金占比 vs 昨日同档
        delta_html = ""
        if prev_size_by_label:
            prev_total = sum(prev_size_by_label.values())
            if prev_total > 0:
                prev_pct = prev_size_by_label.get(label, 0) / prev_total * 100
                diff = pct_size - prev_pct
                if abs(diff) >= 0.05:
                    arrow = "▲" if diff > 0 else "▼"
                    arrow_color = ACCENT_UP if diff > 0 else ACCENT_DN
                    delta_html = (
                        f'<span style="color:{arrow_color};font-size:9px;font-weight:700;'
                        f'background:{arrow_color}1a;padding:0 3px;border-radius:2px;'
                        f'margin-left:2px;line-height:1.2;">{arrow}{abs(diff):.1f}pp</span>'
                    )

        # 两行布局: line1 资金 (亿 + %) + Δpp, line2 计数占比 (只 + %)
        # 字号 10px, 主要数据加粗高亮
        glow = ls["glow"] if ls else TEXT
        rich_sub = (
            f'<div style="line-height:1.4;">'
            f'<div>'
            f'<span style="color:{glow};font-weight:700;font-size:11px;font-family:monospace;">'
            f'{size:,.0f}亿</span>'
            f'<span style="color:{TEXT_DIM};font-size:10px;margin-left:3px;">'
            f'·{pct_size:.1f}%资金</span>'
            f'{delta_html}'
            f'</div>'
            f'<div style="color:{TEXT_DIM};font-size:9px;font-family:monospace;margin-top:1px;">'
            f'{count}只·{pct_count:.0f}%</div>'
            f'</div>'
        )
        with cols[i]:
            st.markdown(kpi_card(
                label,
                f"{count}",
                sub_html=rich_sub,
                sub="",  # escape 一个空串,占位
                color=ls["glow"] if ls else TEXT,
            ), unsafe_allow_html=True)


def _fmt_vol(v):
    if v is None or v == 0:
        return "-"
    v = float(v)
    if v >= 1e8:
        return f"{v/1e8:.2f}亿"
    elif v >= 1e4:
        return f"{v/1e4:.0f}万"
    return f"{int(v)}"


def _slope_color_fn(v):
    """斜率颜色:正=红色,负=绿色,空=灰色"""
    if v is None:
        return TEXT_MUTED
    return ACCENT_UP if v > 0 else ACCENT_DN


def _init_stock_detail_state():
    """2026-07-21 α 靶点: 初始化 stock_detail 跨 Tab 保持的 session_state"""
    if "stock_detail_selected" not in st.session_state:
        st.session_state.stock_detail_selected = None
    if "stock_detail_analysed" not in st.session_state:
        st.session_state.stock_detail_analysed = False
    if "stock_detail_analysed_code" not in st.session_state:
        st.session_state.stock_detail_analysed_code = None


def _build_search_items(df_res: pd.DataFrame) -> list:
    """2026-07-21 α 靶点: 构建 selectbox 搜索选项(code + name)"""
    search_items = []
    for _, r in df_res.iterrows():
        c = str(r["code"]).zfill(6)
        n = str(r.get("name", ""))
        search_items.append(f"{c} {n}")
    return search_items


def _resolve_chip_fallback_label(search_items: list) -> str | None:
    """2026-07-21 α 靶点: 从 session_state.analysed_code 反查 full label (chip 点击后)"""
    chip_code = st.session_state.stock_detail_analysed_code
    if not chip_code:
        return None
    for _lbl in search_items:
        if _lbl.startswith(chip_code):
            return _lbl
    return None


def _render_stock_search_bar(search_items: list) -> tuple:
    """2026-07-21 α 靶点: 渲染搜索栏 (selectbox + 分析按钮)

    Returns:
        (selected, go_btn) 用户当前选中的 label 和是否点了分析
    """
    c1, c2 = st.columns([3, 1])
    with c1:
        _cur_label = st.session_state.stock_detail_selected
        _cur_idx = search_items.index(_cur_label) if _cur_label in search_items else None
        selected = st.selectbox(
            "", search_items,
            index=_cur_idx,
            placeholder="输入代码或中文名称搜索...",
            label_visibility="collapsed",
            key="stock_detail_search",
        )
        if selected:
            st.session_state.stock_detail_selected = selected
        # 🔥 热门 chip 点过后：selected 可能为 None, fallback 为 chip_code 对应 label
        if not selected:
            selected = _resolve_chip_fallback_label(search_items)
    with c2:
        go_btn = st.button(
            "🔍 分析",
            use_container_width=True,
            type="primary",
            key="stock_detail_go",
        )
    return selected, go_btn


def _should_enter_chips_branch(go_btn: bool) -> bool:
    """2026-07-21 α 靶点: 判断是否走“未首次点击分析”分支 (渲染热门 chip)"""
    return (not go_btn) and (not st.session_state.stock_detail_analysed)


def _render_hot_etf_chips(df_res: pd.DataFrame) -> None:
    """2026-07-21 α 靶点: 渲染热门 ETF chip (top 6 按成交额)

    使用 on_click callback (而非 post-button 检查) 保证 streamlit 1.40+
    selectbox widget 同步性。
    """
    st.markdown(
        f'<div style="color:{TEXT_DIM};font-size:13px;text-align:center;padding:30px 0 8px;">'
        f'输入 ETF 代码或中文名称搜索后点击 <b style="color:{TEXT};">🔍 分析</b>·K 线图加载后自动滚到下方'
        f'</div>'
        f'<div style="color:{TEXT_DIM};font-size:10px;margin-bottom:8px;'
        f'text-transform:uppercase;letter-spacing:0.5px;text-align:center;">🔥 热门 ETF · 一键加载</div>',
        unsafe_allow_html=True,
    )
    _top = df_res.sort_values("latest_amount", ascending=False).head(6)
    _cols = st.columns(6, gap="small")

    def _make_chip_handler(_full_label, _code):
        def _handler():
            # Callback runs in guaranteed pre-rerun context (streamlit 1.40+).
            st.session_state.stock_detail_selected = _full_label
            st.session_state.stock_detail_analysed = True
            st.session_state.stock_detail_analysed_code = _code
            st.session_state["stock_detail_search"] = _full_label
        return _handler

    for i, (_, r) in enumerate(_top.iterrows()):
        with _cols[i]:
            _code = str(r["code"]).zfill(6)
            _name = str(r.get("name", ""))[:6]
            _full_label = f"{_code} {r.get('name','')}"
            st.button(
                f"🔥 {_code}",
                key=f"hot_chip_{_code}",
                help=f"{_name} · 点击直接加载 K 线",
                use_container_width=True,
                on_click=_make_chip_handler(_full_label, _code),
            )
    _sub_html = '<div style="display:flex;gap:6px;margin-top:4px;">'
    for i in range(len(_top)):
        _sub_html += f'<div style="flex:1;text-align:center;color:{TEXT_DIM};font-size:9px;font-family:monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:0 2px;">{str(_top.iloc[i].get("name",""))[:5]}</div>'
    _sub_html += '</div>'
    st.markdown(_sub_html, unsafe_allow_html=True)


def _should_skip_analysis_gate(selected, go_btn: bool) -> bool:
    """2026-07-21 α 靶点: chip 点击后免敲门判断 (selected 有值但 go_btn=False)"""
    if not selected or go_btn:
        return False
    sel_code = selected.split()[0].strip().zfill(6)
    return sel_code == st.session_state.get("stock_detail_analysed_code")


def _resolve_selected_label(search_items: list, selected) -> str | None:
    """2026-07-21 α 靶点: 跨 Tab 恢复 selected (从 analysed_code)"""
    if selected:
        return selected
    if not st.session_state.stock_detail_analysed_code:
        return None
    for _lbl in search_items:
        if _lbl.startswith(st.session_state.stock_detail_analysed_code):
            return _lbl
    return None


def _prepare_stock_kline(code: str, etf_name: str) -> dict | None:
    """2026-07-21 α 靶点: 加载 + 计算 ETF K 线指标

    Returns:
        dict 含 kw_show, kw_250, m, latest_price, yesterday_close, change_pct, direction;
        或 None (加载失败)
    """
    with st.spinner(f"获取 {code} {etf_name} K 线数据..."):
        kline = _cached_fetch_kline(code, 250)

    if kline is None or len(kline) < 100:
        st.error(f"无法获取 {code} 的数据,请检查代码或稍后重试")
        return None

    kw = kline.dropna(subset=["close"]).reset_index(drop=True)
    if len(kw) < 250:
        first_row = kw.iloc[:1].copy()
        pad_cnt = 250 - len(kw)
        pads = pd.concat([first_row] * pad_cnt, ignore_index=True)
        kw = pd.concat([pads, kw], ignore_index=True).reset_index(drop=True)
    kw_show = kw.iloc[-120:].reset_index(drop=True)
    kw_250 = kw.iloc[-250:].reset_index(drop=True)
    m = algo.calc_single_etf(kw)
    close = kw_250["close"].astype(float).values
    yesterday_close = close[-2] if len(close) > 1 else close[-1]
    latest_price = close[-1]
    change_pct = ((latest_price - yesterday_close) / yesterday_close) * 100
    direction = "up" if latest_price >= yesterday_close else "dn"
    return {
        "kw_show": kw_show, "kw_250": kw_250, "m": m,
        "latest_price": latest_price, "yesterday_close": yesterday_close,
        "change_pct": change_pct, "direction": direction,
    }


def _render_stock_kline_chart(code: str, kw_show_chart: pd.DataFrame) -> None:
    """2026-07-21 α 靶点: 渲染 K 线图 (主图+成交量副图, CDN lightweight-charts)"""
    chart_html = _kline_chart_html(
        kw_show_chart,
        amount_series=kw_show_chart["amount"] if "amount" in kw_show_chart.columns else None,
    )
    st.components.v1.html(chart_html, height=CHART_KLINE_HEIGHT)


def _enrich_chart_with_latest_amount(code: str, kw_show_chart: pd.DataFrame) -> pd.DataFrame:
    """2026-07-21 α 靶点: 将今日成交额插到 K 线最后一根 (避免估算出错)"""
    kw_show_chart = kw_show_chart.copy()
    try:
        if "amount" not in kw_show_chart.columns:
            kw_show_chart["amount"] = None
        _amt_now = float(_cached_fetch_amount(code) or 0)
        if _amt_now > 0 and len(kw_show_chart) > 0:
            kw_show_chart.iloc[-1, kw_show_chart.columns.get_loc("amount")] = _amt_now
    except Exception:
        pass
    return kw_show_chart


def _render_stock_metric_strip(code: str, df_res: pd.DataFrame, info: dict) -> None:
    """2026-07-21 α 靶点: 渲染一行指标条 (所有技术指标)"""
    m = info["m"]
    if not m:
        return
    latest_price = info["latest_price"]
    change_pct = info["change_pct"]
    direction = info["direction"]
    res_row = df_res[df_res["code"].astype(str).str.zfill(6) == code]
    category = res_row["category"].iloc[0] if len(res_row) > 0 else "-"
    fund_size = res_row["fund_size_yi"].iloc[0] if len(res_row) > 0 else 0

    st.markdown(height_spacer(2), unsafe_allow_html=True)
    metrics = [
        ("最新价", f"{latest_price:.3f}", TEXT),
        ("涨跌幅", "0.00%" if change_pct == 0 else f"{change_pct:+.2f}%",
         ACCENT_UP if direction == "up" else ACCENT_DN),
        ("成交额", f"{(_cached_fetch_amount(code) or 0) / 1e8:.2f}亿", TEXT),
        ("分类", category, TEXT_MUTED),
        ("规模", f"{fund_size:.1f}亿", TEXT),
        ("趋势", m["strength_label"], LABEL_STYLES.get(m["strength_label"], {}).get("glow", TEXT)),
        ("20日斜率", f"{m['slope_20']:.4f}" if m['slope_20'] is not None else "-",
         _slope_color_fn(m.get('slope_20'))),
        ("50日斜率", f"{m['slope_50']:.4f}" if m['slope_50'] is not None else "-",
         _slope_color_fn(m.get('slope_50'))),
        ("120日斜率", f"{m['slope_120']:.4f}" if m['slope_120'] is not None else "-",
         _slope_color_fn(m.get('slope_120'))),
        ("夏普", f"{m['sharpe_composite']:.3f}" if m['sharpe_composite'] is not None else "-", TEXT),
        ("ADX", f"{m['adx']:.2f}" if m['adx'] else "-", TEXT),
        ("60日↑%", f"{m['up_ratio_60']*100:.1f}%" if m['up_ratio_60'] is not None else "-", TEXT),
    ]
    st.markdown(metric_row_html(metrics), unsafe_allow_html=True)


def _render_scroll_to_kline_js() -> None:
    """2026-07-21 α 靶点: 点分析后滚动到 K 线图的 JS (iframe 渲染后才生效)"""
    scroll_js = """
<script>
(function(){
  function go(){
    var sels = [
      'iframe[title*="st.iframe"]',
      'iframe[title*="streamlit"]',
      'div[data-testid="stCustomComponentV1"]',
      'div[data-testid="stMarkdownContainer"]'
    ];
    for (var i=0;i<sels.length;i++){
      var el = document.querySelector(sels[i]);
      if (el) {
        var target = el.closest('section') || el;
        target.scrollIntoView({behavior:'smooth', block:'start'});
        return;
      }
    }
    setTimeout(go, 120);
  }
  setTimeout(go, 250);
})();
</script>
"""
    st.markdown(scroll_js, unsafe_allow_html=True)


def render_stock_detail(df_res: pd.DataFrame):
    """个股 ETF 分析子视图 - 东财风格 (2026-07-21 α 靶点: 275L → 25L 调度器)

    跨顶层 Tab 切换时,搜索词 / 分析结果都会被 session_state 冱底保留。
    六阶段调度:init → search → gate → load → chart → metrics
    """
    st.markdown(f'<div style="margin-bottom:8px;"></div>', unsafe_allow_html=True)

    _init_stock_detail_state()
    search_items = _build_search_items(df_res)

    if not search_items:
        st.markdown(
            f'<div style="color:{TEXT_DIM};font-size:13px;text-align:center;'
            f'padding:40px 0;border:1px dashed {BORDER};border-radius:8px;">'
            f'暂无可分析个股(数据未就绪)<br/>请稍后重试或点击顶部「🔄 数据刷新」</div>',
            unsafe_allow_html=True,
        )
        return

    selected, go_btn = _render_stock_search_bar(search_items)

    if _should_enter_chips_branch(go_btn):
        try:
            _render_hot_etf_chips(df_res)
        except Exception:
            st.markdown(
                f'<div style="color:{TEXT_DIM};font-size:13px;text-align:center;'
                f'padding:40px 0;border:1px dashed {BORDER};border-radius:8px;">'
                f'输入 ETF 代码或中文名称搜索后点击 <b style="color:{TEXT};">🔍 分析</b>,K 线图加载后会自动滚到下方</div>',
                unsafe_allow_html=True,
            )
        return

    if _should_skip_analysis_gate(selected, go_btn):
        go_btn = True
    selected = _resolve_selected_label(search_items, selected)

    if not selected or not go_btn:
        st.session_state.stock_detail_analysed = False
        st.markdown(
            f'<div style="color:{TEXT_DIM};font-size:13px;text-align:center;'
            f'padding:40px 0;border:1px dashed {BORDER};border-radius:8px;">'
            f'输入 ETF 代码或中文名称搜索后点击 <b style="color:{TEXT};">🔍 分析</b>,K 线图加载后会自动滚到下方</div>',
            unsafe_allow_html=True,
        )
        return

    st.session_state.stock_detail_analysed = True
    st.session_state.stock_detail_analysed_code = selected

    code = selected.split()[0].strip().zfill(6)
    etf_name = selected.split(maxsplit=1)[-1] if len(selected.split()) > 1 else ""

    info = _prepare_stock_kline(code, etf_name)
    if info is None:
        return

    kw_show_chart = _enrich_chart_with_latest_amount(code, info["kw_show"])
    _render_stock_kline_chart(code, kw_show_chart)
    _render_scroll_to_kline_js()
    _render_stock_metric_strip(code, df_res, info)



# ============================================================
# 三个顶层 Tab 入口
# ============================================================
def _subview_radio(view_keys, view_labels, state_key, default_idx=0):
    """一个跨 Tab 共享的子视图 radio,带 session_state 保持"""
    if state_key not in st.session_state:
        st.session_state[state_key] = view_keys[default_idx]
    # 优先尝试 st.segmented_control(1.40+ 原生),失败则降级为 radio(1.39)
    # label 用单空格 + label_visibility="collapsed" 完全隐藏(streamlit 1.40+ 不再接受空字符串 label)
    try:
        _sel = st.segmented_control(
            " ", view_labels,
            selection_mode="single",
            default=view_labels[view_keys.index(st.session_state[state_key])]
                    if st.session_state[state_key] in view_keys else view_labels[default_idx],
            key=f"seg_{state_key}",
            label_visibility="collapsed",
        )
        if _sel is None:
            _sel = view_labels[default_idx]
    except Exception:
        # Fallback: 传统 radio(云端 1.39)
        _sel = st.radio(
            " ", view_labels,
            index=view_keys.index(st.session_state[state_key])
                  if st.session_state[state_key] in view_keys else default_idx,
            horizontal=True, label_visibility="collapsed",
            key=f"radio_{state_key}",
        )
    if _sel in view_labels:
        st.session_state[state_key] = view_keys[view_labels.index(_sel)]
    return st.session_state[state_key]


def render_overview(df_res: pd.DataFrame, df_hist: pd.DataFrame):
    """顶层 Tab 【📊 大盘总览】: KPI + 6 档分类子视图(股票详情 / 趋势演变 / 6档列表)"""
    render_kpi(df_res, df_hist)
    # 🚨 板块异动横幅 + 下载按钮 工具行
    _render_anomaly_banner(df_res, df_hist)
    st.markdown(height_spacer(12), unsafe_allow_html=True)

    icons = {
        "超强势":   "🟥",
        "强势":     "🟧",
        "震荡上涨": "🟨",
        "横盘震荡": "⬜",
        "震荡下跌": "🟦",
        "一直下跌": "🟫",
    }
    view_keys = list(LABEL_ORDER)
    view_labels = [f"{icons.get(l, '')}{l}" for l in LABEL_ORDER]

    sel = _subview_radio(view_keys, view_labels, state_key="_overview_view", default_idx=0)
    render_list_view(df_res, label_filter=sel)

    st.markdown(f"""
    <button class="back-to-top" onclick="window.scrollTo({{top:0,behavior:'smooth'}})" title="回到顶部">↑</button>
    """, unsafe_allow_html=True)


def render_history(df_res: pd.DataFrame, df_hist: pd.DataFrame):
    """顶层 Tab 【🔥 趋势演变】: 25天色块热力图"""
    st.markdown(
        f'<div style="color:{TEXT_MUTED};font-size:13px;margin-bottom:8px;">'
        f'<b>🔥 趋势演变</b> · 最近 25 个交易日'
        f'<span style="color:{TEXT_DIM};font-size:10px;margin-left:8px;">'
        f'色块=当天所属档位·鼠标悬停看完整名</span></div>',
        unsafe_allow_html=True,
    )
    render_history_table(df_hist, df_res)

    st.markdown(f"""
    <button class="back-to-top" onclick="window.scrollTo({{top:0,behavior:'smooth'}})" title="回到顶部">↑</button>
    """, unsafe_allow_html=True)


def render_individual(df_res: pd.DataFrame, df_hist: pd.DataFrame):
    """顶层 Tab 【📈 个股分析】: 搜索 + K线 + 指标条"""
    st.markdown(
        f'<div style="color:{TEXT_MUTED};font-size:13px;margin-bottom:8px;">'
        f'<b>📈 个股分析</b> · ETF K线 + 算法指标'
        f'<span style="color:{TEXT_DIM};font-size:10px;margin-left:8px;">'
        f'代码 / 名称 搜索 · 点「分析」加载 K 线</span></div>',
        unsafe_allow_html=True,
    )
    render_stock_detail(df_res)

    st.markdown(f"""
    <button class="back-to-top" onclick="window.scrollTo({{top:0,behavior:'smooth'}})" title="回到顶部">↑</button>
    """, unsafe_allow_html=True)


# ============================================================
# Step3: 加载骨架屏(shimmer 动效)— 复用 KPI/表格区块的视觉占位
# ============================================================
def render_skeleton(n_rows: int = 6, n_cols: int = 7, label: str = "加载中..."):
    """Step3: 渲染骨架屏 — 数据加载时的视觉占位(灰块 + shimmer 渐变动画)

    n_rows: 行数(默认 6 行)
    n_cols: 列数(默认 7 列,匹配 KPI 卡布局)
    label: 顶部提示文字

    调用方在 loading 状态下调用,streamlit 收到数据后再调用真实渲染函数。
    """
    # 顶部 label
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:8px;'
        f'color:{TEXT_DIM};font-size:11px;margin:6px 0 10px 0;">'
        f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;'
        f'background:{ACCENT_UP};animation: skeleton-shimmer 1.6s ease-in-out infinite;"></span>'
        f'<span>{label}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    # 列骨架
    cols = st.columns(n_cols, gap="small")
    for i, c in enumerate(cols):
        with c:
            st.markdown(
                f'<div class="skeleton-bar tall"></div>'
                f'<div class="skeleton-bar medium"></div>'
                f'<div class="skeleton-bar short"></div>',
                unsafe_allow_html=True,
            )
    # 行骨架(下方表格区)
    for _ in range(n_rows):
        st.markdown(
            f'<div style="display:flex;gap:8px;margin-bottom:4px;">'
            f'<div class="skeleton-bar" style="flex:0 0 80px;"></div>'
            f'<div class="skeleton-bar" style="flex:1 1 0;"></div>'
            f'<div class="skeleton-bar" style="flex:0 0 90px;"></div>'
            f'<div class="skeleton-bar" style="flex:0 0 80px;"></div>'
            f'<div class="skeleton-bar" style="flex:0 0 100px;"></div>'
            f'</div>',
            unsafe_allow_html=True,
        )
