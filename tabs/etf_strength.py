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
)
from lib import algorithm as algo
from lib.chart_kline import _kline_chart_html
from lib.ui_components import label_badge_html, kpi_card, metric_row_html


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_fetch_kline(code: str, min_len: int):
    """缓存 K 线数据(优先腾讯快照,更稳定),避免跨 tab 切换重复拉取卡死"""
    try:
        k = algo.fetch_kline_tencent(code)
        if k is not None and len(k) >= 120:
            return k
    except Exception:
        pass
    k = algo.fetch_kline(code, min_len)
    return k


@st.cache_data(ttl=600, show_spinner=False)
def _cached_fetch_amount(code: str):
    """成交额缓存,避免每次进入个股分析重复拉取"""
    return algo.fetch_amount(code)


@st.cache_data(ttl=600, show_spinner=False)
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


@st.cache_data(ttl=600, show_spinner=False)
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


def render_table(df: pd.DataFrame):
    if df.empty:
        st.info("无匹配数据")
        return

    cols_order = [
        "code", "name", "strength_label", "category",
        "latest_close", "latest_amount",
        "fund_size_yi",
    ]
    show = df[[c for c in cols_order if c in df.columns]].copy()
    show = show.rename(columns={
        "code": "代码", "name": "名称", "strength_label": "趋势",
        "category": "分类",
        "latest_close": "最新价", "latest_amount": "成交额(亿)",
        "fund_size_yi": "规模(亿)",
    })
    show["趋势"] = show["趋势"].apply(label_badge_html)

    # 格式化
    def fmt_price(v):
        if pd.isna(v) or float(v) == 0: return "-"
        return f"{float(v):.3f}"

    def fmt_vol(v):
        if pd.isna(v) or float(v) == 0: return "-"
        vol = float(v)
        if vol >= 1e8:
            return f"{vol/1e8:.1f}亿"
        elif vol >= 1e4:
            return f"{vol/1e4:.1f}万"
        else:
            return f"{int(vol)}"

    def fmt_yi(v):
        if pd.isna(v): return "-"
        return f"{float(v):.1f}"

    if "代码" in show.columns:
        show["代码"] = show["代码"].apply(lambda v: f"{int(v)}" if pd.notna(v) else "-")
    if "最新价" in show.columns:
        show["最新价"] = show["最新价"].apply(fmt_price)
    if "成交额(亿)" in show.columns:
        def _fmt_amount(v):
            if pd.isna(v) or float(v) == 0:
                return "-"
            yuan = float(v)
            if yuan >= 1e8:
                return f"{yuan/1e8:.2f}亿"
            elif yuan >= 1e4:
                return f"{yuan/1e4:.1f}万"
            return f"{int(yuan)}"
        show["成交额(亿)"] = show["成交额(亿)"].apply(_fmt_amount)
    if "规模(亿)" in show.columns:
        show["规模(亿)"] = show["规模(亿)"].apply(fmt_yi)

    st.markdown(
        f'<div style="color:{TEXT_DIM};font-size:10px;margin-bottom:4px;'
        f'display:flex;align-items:center;gap:8px;">'
        f'<span>共 <b style="color:{TEXT};">{len(show)}</b> 只 · 点击表头排序 · 输关键词过滤</span>'
        f'<input type="search" id="etf_search_{id(df)}" placeholder="🔍 过滤代码/名称/趋势" '
        f'style="margin-left:auto;background:{BG_PANEL};color:{TEXT};border:1px solid {BORDER};'
        f'border-radius:4px;padding:2px 8px;font-size:11px;width:240px;outline:none;" '
        f'autocomplete="off"/>'
        f'<span id="etf_search_count_{id(df)}" style="color:{TEXT_DIM};font-size:10px;"></span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="etf-table-wrap" data-uid="{id(df)}">'
        f'{show.to_html(escape=False, index=False, border=0, classes="etf-table")}'
        f'</div>',
        unsafe_allow_html=True,
    )
    # 客户端过滤脚本：实时过滤行,不需后端 round-trip
    st.markdown(f"""
    <script>
    (function(){{
      var uid = '{id(df)}';
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
      // 按 ESC 清除
      input.addEventListener('keydown', function(e){{
        if (e.key === 'Escape') {{ input.value = ''; apply(); }}
      }});

      // === 表头点击排序 ===
      // 把初始 th 的原始内容寫到 dataset.label,这样排序时 label 不会被丢掉
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
            // 清除同辈的排序状态
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
              // 尝试按数字排序(去掉千分位/亿/万等单位后缀)
              var an = parseFloat(av.replace(/[^\\d.\\-]/g, ''));
              var bn = parseFloat(bv.replace(/[^\\d.\\-]/g, ''));
              var isNum = !isNaN(an) && !isNaN(bn) && /[\\d.]/.test(av) && /[\\d.]/.test(bv);
              if (isNum) return dir === 'asc' ? an - bn : bn - an;
              return dir === 'asc' ? av.localeCompare(bv, 'zh-CN') : bv.localeCompare(av, 'zh-CN');
            }});
            rowsArr.forEach(function(r){{ tbody.appendChild(r); }});
          }} else {{
            // 复位到初始顺序（重读原 DOM）
            rowsArr.sort(function(a,b){{
              return (parseInt(a.dataset.origIdx||0) - parseInt(b.dataset.origIdx||0));
            }});
            rowsArr.forEach(function(r){{ tbody.appendChild(r); }});
          }}
          apply();  // 重排后重新应用过滤
        }});
      }});
      // 保存原始顺序
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
      .etf-table th:last-child {{ text-align: right; }}
      .etf-table td {{
        padding: 5px 8px; border-bottom: 1px solid #151b2a;
        color: {TEXT}; white-space: nowrap; font-feature-settings: "tnum";
        font-size: 11px;
      }}
      .etf-table td:nth-child(2) {{ max-width: 220px; overflow: hidden; text-overflow: ellipsis; }}
      .etf-table tr:hover td {{ background: {BG_PANEL_HI}; }}
      .etf-table tr:last-child td {{ border-bottom: none; }}
      /* 回到顶部浮钮(右下角,避免跟底部 footer 重叠) */
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
    </style>
    """, unsafe_allow_html=True)


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
    df = df_hist.copy()

    # === 构建展示表(缓存加速) ===
    _pts_tuple = tuple(points)
    _sel_dates_tuple = tuple(selected_points)
    _lbl_filter_list = []  # 不再趋势筛选

    # 首次拼 207×25=5175 个 span 会~1秒，加 spinner + 准迟避免闪屏
    import time
    t0 = time.time()
    with st.spinner(f"拼装 {len(df_hist)}×{len(selected_points)} 趋势矩阵..."):
        html_body, n_etf, n_days = _build_history_html(
            df_hist, _pts_tuple, df_res, _sel_dates_tuple, _lbl_filter_list
        )
    render_ms = int((time.time() - t0) * 1000)

    st.markdown(
        f'<div style="color:{TEXT_DIM};font-size:11px;margin-bottom:6px;'
        f'display:flex;justify-content:space-between;align-items:center;">'
        f'<span style="font-weight:500;">共 {n_etf} 只 · {n_days} 天 '
        f'<span style="color:{TEXT_DIM};font-size:10px;margin-left:6px;">'
        f'拼装 {render_ms}ms</span></span>'
        f'<span style="font-size:11px;letter-spacing:0.3px;">'
        f'<span style="background:#ff1a3d;color:#fff;padding:1px 6px;border-radius:3px;font-size:10px;margin-right:2px;">超强势</span> '
        f'<span style="background:#ff6b00;color:#fff;padding:1px 6px;border-radius:3px;font-size:10px;margin-right:2px;">强势</span> '
        f'<span style="background:#ffc107;color:#0a0e1a;padding:1px 6px;border-radius:3px;font-size:10px;margin-right:2px;">震荡上涨</span> '
        f'<span style="background:#2a334a;color:#c5c8d6;padding:1px 6px;border-radius:3px;font-size:10px;margin-right:2px;">横盘震荡</span> '
        f'<span style="background:#3a7bd5;color:#fff;padding:1px 6px;border-radius:3px;font-size:10px;margin-right:2px;">震荡下跌</span> '
        f'<span style="background:#1c2538;color:#9aaac0;padding:1px 6px;border-radius:3px;font-size:10px;">一直下跌</span>'
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
        padding: 3px 1px; border-bottom: 1px solid rgba(31,38,56,0.4);
        color: {TEXT}; white-space: nowrap;
        font-size: 11px; text-align: center;
        line-height: 1.4;
        overflow: hidden; text-overflow: ellipsis;
      }}
      .compact-table td:nth-child(-n+2) {{ text-align: left !important; padding-left: 6px; padding-right: 0 !important; font-size: 11px; }}
      .compact-table th:first-child, .compact-table td:first-child {{
        width: 56px !important;
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
        width: 120px !important;
        padding-left: 2px !important;
      }}
      /* 日期列宽度统一 */
      .compact-table th:nth-child(n+3),
      .compact-table td:nth-child(n+3) {{
        width: 38px !important;
        padding: 3px 1px !important;
      }}
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
    """详细列表子视图 - 含行业分类快速过滤"""
    # 行业过滤:仅在有 category 列时启用
    cats = sorted(df_res["category"].dropna().astype(str).unique().tolist()) \
        if "category" in df_res.columns else []
    cat_filter: list[str] = []
    if cats:
        c1, c2, c3 = st.columns([2, 2, 6])
        with c1:
            cat_filter = st.multiselect(
                "行业", cats, default=[],
                placeholder="全部行业", label_visibility="collapsed",
                key=f"cat_filter_{label_filter or 'all'}",
            )
        with c2:
            pass  # 占位 · 后续可加排序方向按钮
        with c3:
            if cat_filter:
                st.markdown(
                    f'<div style="color:{TEXT_DIM};font-size:10px;padding-top:8px;">'
                    f'已选行业: <b style="color:{TEXT};">{len(cat_filter)}</b> 个</div>',
                    unsafe_allow_html=True,
                )

    df_view = _prepare_list_view(df_res, label_filter, tuple(cat_filter))

    title_extra = f" · {label_filter}" if label_filter else ""
    if cat_filter:
        title_extra += f" · {len(cat_filter)}个行业"

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
            s = LABEL_STYLES.get(top["strength_label"])
            bg = s["glow"] if s else "#fff"
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


def _render_anomaly_banner(df_res: pd.DataFrame, df_hist: pd.DataFrame):
    """KPI 卡下面一行「🚨 板块异动」横幅 + 「⬇️ 导出 CSV」按钮

    业务上接 6 档 Δpp，今天最强的资金动向和最狠的流出用一句换一句话讲完。
    """
    total_size = df_res["fund_size_yi"].sum() if "fund_size_yi" in df_res.columns else 0
    today = {}
    for label in LABEL_ORDER:
        sub = df_res[df_res["strength_label"] == label]
        today[label] = sub["fund_size_yi"].sum() if "fund_size_yi" in sub.columns else 0

    # 昨日同一档位资金占比
    prev_pct = {}
    if df_hist is not None and not df_hist.empty and total_size > 0:
        try:
            points = [c for c in df_hist.columns if c not in ("code", "name")]
            if len(points) >= 2:
                prev_point = points[-2]
                size_map = dict(zip(df_res["code"].astype(str), df_res["fund_size_yi"]))
                prev_total = 0.0
                for lbl in LABEL_ORDER:
                    cnt_df = df_hist[df_hist[prev_point] == lbl]
                    s = sum(float(size_map.get(str(r["code"]), 0) or 0) for _, r in cnt_df.iterrows())
                    prev_pct[lbl] = s
                    prev_total += s
                if prev_total > 0:
                    prev_pct = {k: v / prev_total * 100 for k, v in prev_pct.items()}
        except Exception:
            prev_pct = {}

    diffs = []
    for label in LABEL_ORDER:
        cur_pct = (today[label] / total_size * 100) if total_size else 0
        pp = cur_pct - prev_pct.get(label, 0)
        if pp != 0:
            diffs.append((label, pp, cur_pct))
    diffs.sort(key=lambda x: x[1])
    if not diffs:
        return
    top_in = diffs[-1]   # 净流入最多
    top_out = diffs[0]   # 净流出最多

    def _chip(label, pp):
        ls = LABEL_STYLES.get(label)
        fg = ls["glow"] if ls else TEXT
        sign = "+" if pp > 0 else ""
        return (
            f'<span style="background:{fg}22;color:{fg};'
            f'padding:1px 8px;border-radius:10px;'
            f'font-size:11px;font-weight:600;margin-right:6px;">'
            f'{label}{sign}{pp:.1f}pp</span>'
        )

    banner_html = (
        f'<div style="display:flex;align-items:center;gap:12px;margin-top:8px;'
        f'background:linear-gradient(90deg,rgba(255,77,79,0.06),rgba(34,197,94,0.06));'
        f'border:1px solid {BORDER};border-radius:6px;padding:8px 12px;">'
        f'<span style="font-size:13px;">🚨 <b style="color:{TEXT};">板块异动</b></span>'
        f'<span style="color:{TEXT_DIM};font-size:11px;">'
        f'<span style="color:{ACCENT_UP};font-weight:600;">资金流入</span> '
        f'{_chip(top_in[0], top_in[1])} '
        f'<span style="color:{TEXT_DIM};">·</span> '
        f'<span style="color:{ACCENT_DN};font-weight:600;">资金流出</span> '
        f'{_chip(top_out[0], top_out[1])}'
        f'</span>'
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

    # 注入 KPI 样式
    st.markdown(f"""
    <style>
      .kpi-card {{
        transition: all 0.2s cubic-bezier(0.4,0,0.2,1) !important;
      }}
      /* 有鼠标时hover,手机上点击触发active */
      @media (hover: hover) {{
        .kpi-card:hover {{
          background:{BG_PANEL_HI} !important;
          border-color:{BORDER_HI} !important;
          transform: translateY(-2px);
          box-shadow: 0 4px 12px rgba(0,0,0,0.3) !important;
        }}
        .kpi-card:hover .kpi-value {{
          filter: brightness(1.15);
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
                    arrow_color = (ls["glow"] if ls else TEXT) if diff > 0 else "#22c55e"
                    delta_html = (
                        f' <span style="color:{arrow_color};font-size:10px;font-weight:600;">'
                        f'{arrow}{abs(diff):.1f}pp</span>'
                    )

        rich_sub = (
            f'{size:,.0f}亿 · '
            f'<span style="color:{ls["glow"] if ls else TEXT};font-weight:600;">'
            f'{pct_size:.1f}%资金</span>'
            f'{delta_html}'
            f'<span style="color:{TEXT_DIM};font-size:9px;margin-left:4px;">'
            f'({count}只·{pct_count:.0f}%)</span>'
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


def render_stock_detail(df_res: pd.DataFrame):
    """个股 ETF 分析子视图 - 东财风格

    跨顶层 Tab 切换时,搜索词 / 分析结果都会被 session_state 冱底保留,
    用户从大盘总览切出去再切回来还能看到上次看的 K 线图。
    """
    st.markdown(f'<div style="margin-bottom:8px;"></div>', unsafe_allow_html=True)

    # 搜索栏
    search_items = []
    for _, r in df_res.iterrows():
        c = str(r["code"]).zfill(6)
        n = str(r.get("name", ""))
        search_items.append(f"{c} {n}")

    # 🔧 修复：跨顶层 Tab 切换后 widget key 会丢，用 session_state 离冱
    # 初始化选中状态
    if "stock_detail_selected" not in st.session_state:
        st.session_state.stock_detail_selected = None
    if "stock_detail_analysed" not in st.session_state:
        st.session_state.stock_detail_analysed = False
    if "stock_detail_analysed_code" not in st.session_state:
        st.session_state.stock_detail_analysed_code = None

    c1, c2 = st.columns([3, 1])
    with c1:
        # selectbox 的 index 从 session_state 反查，跨 Tab 后保留
        _cur_label = st.session_state.stock_detail_selected
        _cur_idx = search_items.index(_cur_label) if _cur_label in search_items else None
        selected = st.selectbox(
            "", search_items,
            index=_cur_idx,
            placeholder="输入代码或中文名称搜索...",
            label_visibility="collapsed",
            key="stock_detail_search",
        )
        # 回到顶部后会丢，事后同步回去
        if selected:
            st.session_state.stock_detail_selected = selected
    with c2:
        go_btn = st.button(
            "🔍 分析",
            use_container_width=True,
            type="primary",
            key="stock_detail_go",
        )

    # 用户未首次点击「分析」且 session_state 中有最近看过的 code —— 自动恢复
    if not go_btn and not st.session_state.stock_detail_analysed:
        st.markdown(
            f'<div style="color:{TEXT_DIM};font-size:13px;text-align:center;'
            f'padding:40px 0;border:1px dashed {BORDER};border-radius:8px;">'
            f'输入 ETF 代码或中文名称搜索后点击 <b style="color:{TEXT};">🔍 分析</b>,K 线图加载后会自动滚到下方</div>',
            unsafe_allow_html=True,
        )
        st.text_input("_stock_detail_hidden", label_visibility="collapsed", disabled=True, key="_hidden_widget")
        return

    # 若 session_state 中存了上次分析的 code,可以跨 Tab 恢复
    if not selected and st.session_state.stock_detail_analysed_code:
        selected = st.session_state.stock_detail_analysed_code
    if not selected or not go_btn:
        # 未点击「分析」 && session 也没缓存 code  —— 占位提示
        st.session_state.stock_detail_analysed = False
        st.markdown(
            f'<div style="color:{TEXT_DIM};font-size:13px;text-align:center;'
            f'padding:40px 0;border:1px dashed {BORDER};border-radius:8px;">'
            f'输入 ETF 代码或中文名称搜索后点击 <b style="color:{TEXT};">🔍 分析</b>,K 线图加载后会自动滚到下方</div>',
            unsafe_allow_html=True,
        )
        st.text_input("_stock_detail_hidden", label_visibility="collapsed", disabled=True, key="_hidden_widget")
        return

    st.session_state.stock_detail_analysed = True
    st.session_state.stock_detail_analysed_code = selected  # 冱底
    # 每次点分析都滚动到 K 线图
    _scroll_after_render = True

    st.session_state.stock_detail_analysed = True
    # 每次点分析都滚动到 K 线图(不靠"首次"那个不可靠的标志位)
    _scroll_after_render = True

    code = selected.split()[0].strip().zfill(6)
    etf_name = selected.split(maxsplit=1)[-1] if len(selected.split()) > 1 else ""

    with st.spinner(f"获取 {code} {etf_name} K 线数据..."):
        kline = _cached_fetch_kline(code, 250)

    if kline is None or len(kline) < 100:
        st.error(f"无法获取 {code} 的数据,请检查代码或稍后重试")
        return

    kw = kline.dropna(subset=["close"]).reset_index(drop=True)
    if len(kw) < 250:
        # 不足 250 日:前向填充到 250,让 calc_single_etf 能跑
        first_row = kw.iloc[:1].copy()
        pad_cnt = 250 - len(kw)
        pads = pd.concat([first_row] * pad_cnt, ignore_index=True)
        kw = pd.concat([pads, kw], ignore_index=True).reset_index(drop=True)
    # K线默认显示最近120根(东财风格),250根太挤会变成细线
    kw_show = kw.iloc[-120:].reset_index(drop=True)
    kw_250 = kw.iloc[-250:].reset_index(drop=True)
    m = algo.calc_single_etf(kw)
    close = kw_250["close"].astype(float).values
    o = kw_250["open"].astype(float).values
    vol = kw_250["volume"].astype(float).values if "volume" in kw_250.columns else [0]
    latest_price = close[-1]
    latest_vol = vol[-1]
    yesterday_close = close[-2] if len(close) > 1 else latest_price
    change_pct = ((latest_price - yesterday_close) / yesterday_close) * 100
    direction = "up" if latest_price >= yesterday_close else "dn"

    # 从 df_res 拿信息
    res_row = df_res[df_res["code"].astype(str).str.zfill(6) == code]
    category = res_row["category"].iloc[0] if len(res_row) > 0 else "-"
    fund_size = res_row["fund_size_yi"].iloc[0] if len(res_row) > 0 else 0

    # === K 线图(主图+成交量副图,CDN lightweight-charts) ===
    # 将"今日成交额"插到 kw_show 的最后一根上(历史处仍依赖估算 fallback,
    # 至少最新一日是真实成交额)
    kw_show_chart = kw_show.copy()
    try:
        if "amount" not in kw_show_chart.columns:
            kw_show_chart["amount"] = None
        # 最新一日: 取自 df_res 的 latest_amount,或补拉一次
        _amt_now = float(_cached_fetch_amount(code) or 0)
        if _amt_now > 0 and len(kw_show_chart) > 0:
            kw_show_chart.iloc[-1, kw_show_chart.columns.get_loc("amount")] = _amt_now
    except Exception:
        pass

    chart_html = _kline_chart_html(
        kw_show_chart,
        amount_series=kw_show_chart["amount"] if "amount" in kw_show_chart.columns else None,
    )
    st.components.v1.html(chart_html, height=CHART_KLINE_HEIGHT)

    # 点分析后自动滚动到 K 线图(iframe 渲染后才生效)
    if _scroll_after_render:
        # 使用更稳定的锚点选择器 + 三个备选选择器跨 Streamlit 版本
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
        // 找到包含 K 线图的最近组件容器,在其上面洨动
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

    # === 指标条(合并为一行,含所有指标) ===
    if m:
        st.markdown(f'<div style="height:2px"></div>', unsafe_allow_html=True)

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



# ============================================================
# 三个顶层 Tab 入口
# ============================================================
def _subview_radio(view_keys, view_labels, state_key, default_idx=0):
    """一个跨 Tab 共享的子视图 radio,带 session_state 保持"""
    if state_key not in st.session_state:
        st.session_state[state_key] = view_keys[default_idx]
    # 优先尝试 st.segmented_control(1.40+ 原生),失败则降级为 radio(1.39)
    try:
        _sel = st.segmented_control(
            "", view_labels,
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
            "", view_labels,
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
    st.markdown(f'<div style="height:12px"></div>', unsafe_allow_html=True)

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
