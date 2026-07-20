"""
K线图组件 — TradingView lightweight-charts via CDN

返回完整 HTML 字符串,调用方用 st.components.v1.html 嵌入。

依赖: pandas + 不需要任何额外包(CDN 加载 lightweight-charts@4.2.1)

【2026-07-20 阶段 2 靶点 2-A】重构 _kline_chart_html 361 行 → 4 个子函数
"""
import json

import pandas as pd

from lib.constants import (
    BG_PANEL, BORDER, TEXT, TEXT_MUTED, TEXT_DIM,
    KLINE_UP_COLOR, KLINE_DOWN_COLOR, MA_COLORS,
    CHART_KLINE_INNER_HEIGHT, KLINE_BAR_SPACING, KLINE_VISIBLE_BARS,
    KLINE_PRICE_TOP_MARGIN, KLINE_PRICE_BOTTOM_MARGIN,
    KLINE_VOLUME_TOP_MARGIN, KLINE_VOLUME_BOTTOM_MARGIN,
)


# ============================================================================
# 2026-07-20 阶段 2 靶点 2-A: 拆分 _kline_chart_html
# 拆为 4 个子函数: 准备数据 + HTML head/body + JS script
# 主函数变成组装器, 381 行 → 17 行
# ============================================================================

def _prepare_chart_data(kw_250, amount_series):
    """准备 K 线图所需的全部 JSON 数据

    Returns:
        tuple: (candle_json, vol_json, amt_json, ma_json, vol_ma_json, ma_color_js)
    """
    cl = kw_250["close"].astype(float).values
    op = kw_250["open"].astype(float).values
    hi = kw_250["high"].astype(float).values
    lo = kw_250["low"].astype(float).values
    dates = kw_250["date"].tolist()
    date_strs = [str(d)[:10] for d in dates]
    vol = kw_250["volume"].astype(float).values if "volume" in kw_250.columns else None
    n = len(cl)

    # 真实成交额: 按行对齐传进去
    amt_arr = None
    if amount_series is not None:
        try:
            amt_arr = [float(x) if x is not None and not pd.isna(x) else None
                       for x in amount_series]
        except Exception:
            amt_arr = None

    # K线数据
    candle_data = []
    for i in range(n):
        candle_data.append({
            "time": date_strs[i],
            "open": round(float(op[i]), 4),
            "high": round(float(hi[i]), 4),
            "low": round(float(lo[i]), 4),
            "close": round(float(cl[i]), 4),
        })

    # 成交量(涨跌色)
    vol_data = []
    if vol is not None:
        for i in range(n):
            vol_data.append({
                "time": date_strs[i],
                "value": round(float(vol[i]), 2),
                "color": KLINE_UP_COLOR if cl[i] >= op[i] else KLINE_DOWN_COLOR,
            })

    # 均线
    def _line_data(series, nd):
        s = pd.Series(series).rolling(nd).mean()
        return [{"time": date_strs[i], "value": round(float(s[i]), 4)}
                for i in range(n) if not pd.isna(s[i])]

    candle_json = json.dumps(candle_data)
    vol_json = json.dumps(vol_data)
    # 真实成交额: 有则传, None 表达为 null
    if amt_arr is not None:
        amt_data = [{"time": date_strs[i], "value": amt_arr[i]}
                    for i in range(n) if amt_arr[i] is not None]
    else:
        amt_data = []
    amt_json = json.dumps(amt_data)
    ma_json = {nd: json.dumps(_line_data(cl, nd)) for nd in MA_COLORS}
    vol_ma_json = {}
    if vol is not None:
        for nd in (5, 10):
            vol_ma_json[nd] = json.dumps(_line_data(vol, nd))

    # MA 颜色 JS 字典
    ma_color_js = "{" + ",".join(f"{nd}: '{c}'" for nd, c in MA_COLORS.items()) + "}"

    return candle_json, vol_json, amt_json, ma_json, vol_ma_json, ma_color_js


def _build_html_head():
    """HTML 头部 + CSS 样式 + toolbar + OHLCV bar (静态, 无参数)

    颜色变量从 lib/constants 嵌入, 仅包含 layout 部分。
    """
    return f'''<!DOCTYPE html>
<html>
<head>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#0f111b; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }}
  .toolbar {{
    display:flex; align-items:center; justify-content:space-between;
    background:#0f111b; padding:6px 8px; border-bottom:1px solid #1f2638;
    color:#c8c8c8; font-size:12px;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  }}
  .toolbar .left {{ display:flex; gap:14px; align-items:center; }}
  .toolbar .right {{ display:flex; gap:4px; }}
  .toolbar .btn {{
    background:#1a1f2e; color:#c8c8c8; border:1px solid #2a334a;
    padding:3px 10px; border-radius:3px; cursor:pointer; font-size:11px;
  }}
  .toolbar .btn:hover {{ background:#2a334a; color:#fff; }}
  .toolbar .btn.active {{ background:#ef4444; color:#fff; border-color:#ef4444; }}
  .legend {{ display:inline-flex; align-items:center; gap:4px; font-size:11px; }}
  .legend .dot {{ width:8px; height:2px; display:inline-block; }}
  #chart {{ width:100%; height:{CHART_KLINE_INNER_HEIGHT}px; }}
  .ohlcv-bar {{
    display:flex; gap:16px; padding:6px 8px; background:#0f111b;
    border-top:1px solid #1f2638; font-size:11px; color:#54586b;
  }}
  .ohlcv-bar .item {{ display:flex; flex-direction:column; min-width:60px; }}
  .ohlcv-bar .label {{ font-size:10px; color:#54586b; }}
  .ohlcv-bar .val {{ font-family:monospace; font-size:12px; font-weight:600; }}
</style>
</head>
<body>
<div class="toolbar">
  <div class="left" id="legend"></div>
  <div class="right">
    <span class="btn active" id="period_day">日K</span>
    <span class="btn" id="period_week">周K</span>
    <span class="btn" id="period_month">月K</span>
    <span class="btn" id="period_fit">复权</span>
  </div>
</div>
<div id="chart"></div>
<div class="ohlcv-bar" id="ohlcv_bar">
  <div class="item"><span class="label">日期</span><span class="val" id="v_date">—</span></div>
  <div class="item"><span class="label">开盘</span><span class="val" id="v_open">—</span></div>
  <div class="item"><span class="label">收盘</span><span class="val" id="v_close">—</span></div>
  <div class="item"><span class="label">最高</span><span class="val" id="v_high">—</span></div>
  <div class="item"><span class="label">最低</span><span class="val" id="v_low">—</span></div>
  <div class="item"><span class="label">振幅</span><span class="val" id="v_amp">—</span></div>
  <div class="item"><span class="label">涨跌幅</span><span class="val" id="v_chg">—</span></div>
  <div class="item"><span class="label">涨跌额</span><span class="val" id="v_chg_amt">—</span></div>
  <div class="item"><span class="label">成交量</span><span class="val" id="v_vol">—</span></div>
  <div class="item"><span class="label">成交额</span><span class="val" id="v_amt">—</span></div>
</div>'''


def _build_js_script(candle_json, vol_json, amt_json, ma_json, vol_ma_json, ma_color_js):
    """完整 JS 渲染脚本: LightweightCharts 配置 + candleSeries/volSeries/maSeries/aggregate/setPeriod

    注: 这是嵌入 HTML 的 JS 字符串, 不实际拆分成多个 JS 函数 (避免 Python f-string 转义复杂度)。
    """
    return f'''
<script src="https://unpkg.com/lightweight-charts@4.2.1/dist/lightweight-charts.standalone.production.js"></script>
<script>
(function(){{
  const RAW = {candle_json};
  const VOL = {vol_json};
  const AMT = {amt_json};
  const MA_DATA = {{ {",".join(f"{nd}: {ma_json[nd]}" for nd in MA_COLORS)} }};
  const VOL_MA_DATA = {{ {",".join(f"{nd}: {vol_ma_json.get(nd, '[]')}" for nd in (5, 10))} }};
  const MA_COLORS = {ma_color_js};
  const dataMap = {{}};
  const volMap = {{}};
  const amtMap = {{}};
  RAW.forEach((d) => {{ dataMap[d.time] = d; }});
  VOL.forEach((d) => {{ volMap[d.time] = d; }});
  AMT.forEach((d) => {{ amtMap[d.time] = d; }});

  // 渲染图例
  const legendEl = document.getElementById('legend');
  Object.keys(MA_COLORS).forEach(nd => {{
    legendEl.insertAdjacentHTML('beforeend',
      '<span class="legend"><span class="dot" style="background:' + MA_COLORS[nd] + '"></span>MA' + nd + '</span>');
  }});

  const chart = LightweightCharts.createChart(document.getElementById('chart'), {{
    layout: {{
      textColor: '#c8c8c8',
      background: {{ type: 'solid', color: '#0f111b' }},
      fontSize: 11,
    }},
    rightPriceScale: {{
      scaleMargins: {{ top: {KLINE_PRICE_TOP_MARGIN}, bottom: {KLINE_PRICE_BOTTOM_MARGIN} }},
      borderColor: '#1f2638',
      textColor: '#54586b',
      alignLabels: true,
      entireTextOnly: true,
      minimumWidth: 60,
    }},
    leftPriceScale: {{ visible: false }},
    timeScale: {{
      borderColor: '#1f2638',
      timeVisible: false,
      secondsVisible: false,
      textColor: '#54586b',
      barSpacing: {KLINE_BAR_SPACING},
      minBarSpacing: 4,
      fixLeftEdge: true,
      fixRightEdge: true,
    }},
    gridVertLines: {{ color: '#1a1f2e', style: 2 }},
    gridHorzLines: {{ color: '#1a1f2e', style: 2 }},
    crosshair: {{
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: {{ color: '#54586b', width: 1, style: LightweightCharts.LineStyle.Dashed, labelBackgroundColor: '#2a334a' }},
      horzLine: {{ color: '#54586b', width: 1, style: LightweightCharts.LineStyle.Dashed, labelBackgroundColor: '#2a334a' }},
    }},
    handleScroll: {{ vertTouchDrag: true, pressedMouseMove: true, mouseWheel: true }},
    handleScale: {{ axisPressedMouse: {{ time: true, price: true }}, pinch: true, mouseWheel: true }},
    kineticScroll: {{ mouse: true, touch: true }},
  }});

  // K线主图
  // K线主图 — 固定 3 位小数精度,确保 Y 轴显示 1.250 1.300 1.350 1.400 1.450 1.500 而不是 1.27 1.50
  const candleSeries = chart.addCandlestickSeries({{
    upColor: '{KLINE_UP_COLOR}', downColor: '{KLINE_DOWN_COLOR}',
    borderUpColor: '{KLINE_UP_COLOR}', borderDownColor: '{KLINE_DOWN_COLOR}',
    wickUpColor: '{KLINE_UP_COLOR}', wickDownColor: '{KLINE_DOWN_COLOR}',
    priceFormat: {{
      type: 'price',
      precision: 3,
      minMove: 0.001,
    }},
  }});
  candleSeries.setData(RAW);

  // 均线(动态生成)
  const maSeries = {{}};
  Object.keys(MA_DATA).forEach(nd => {{
    const n = parseInt(nd);
    const s = chart.addLineSeries({{
      color: MA_COLORS[n],
      lineWidth: 1,
      title: 'MA' + n,
      priceLineVisible: false,
      lastValueVisible: true,
    }});
    s.setData(MA_DATA[nd]);
    maSeries[n] = s;
  }});

  // 成交量副图
  const volSeries = chart.addHistogramSeries({{
    priceFormat: {{ type: 'volume' }},
    priceScaleId: 'volume',
  }});
  chart.priceScale('volume').applyOptions({{
    scaleMargins: {{ top: {KLINE_VOLUME_TOP_MARGIN}, bottom: {KLINE_VOLUME_BOTTOM_MARGIN} }},
    borderColor: '#1f2638',
  }});
  volSeries.setData(VOL);

  // 成交量均线(绑定到 volume scale,关键修复)
  Object.keys(VOL_MA_DATA).forEach(nd => {{
    const n = parseInt(nd);
    if (VOL_MA_DATA[nd].length > 0) {{
      const colorMap = {{5: '#f59e0b', 10: '#60a5fa'}};
      const s = chart.addLineSeries({{
        color: colorMap[n],
        lineWidth: 1,
        priceScaleId: 'volume',
        priceLineVisible: false,
        lastValueVisible: false,
      }});
      s.setData(VOL_MA_DATA[nd]);
    }}
  }});

  // 顶部 OHLCV 数据栏更新
  const $ = id => document.getElementById(id);
  function fmtVol(v) {{
    if (v >= 1e8) return (v/1e8).toFixed(2) + '亿';
    if (v >= 1e4) return (v/1e4).toFixed(0) + '万';
    return String(v);
  }}
  function fmtAmt(v) {{
    if (v >= 1e12) return (v/1e12).toFixed(2) + '万亿';
    if (v >= 1e8) return (v/1e8).toFixed(2) + '亿';
    if (v >= 1e4) return (v/1e4).toFixed(0) + '万';
    return String(v);
  }}

  function updateBar(d, prev, v) {{
    if (!d) return;
    const chg = prev ? ((d.close-prev.close)/prev.close*100) : 0;
    const chgAmt = prev ? (d.close-prev.close) : 0;
    const up = chg >= 0;
    $('v_date').textContent = d.time;
    $('v_open').textContent = d.open.toFixed(3);
    $('v_close').textContent = d.close.toFixed(3);
    $('v_high').textContent = d.high.toFixed(3);
    $('v_low').textContent = d.low.toFixed(3);
    $('v_amp').textContent = ((d.high-d.low)/d.open*100).toFixed(2)+'%';
    $('v_chg').textContent = (chg>=0?'+':'') + chg.toFixed(2) + '%';
    $('v_chg_amt').textContent = (chgAmt>=0?'+':'') + chgAmt.toFixed(3);
    $('v_close').style.color = up ? '{KLINE_UP_COLOR}' : '{KLINE_DOWN_COLOR}';
    $('v_chg').style.color = up ? '{KLINE_UP_COLOR}' : '{KLINE_DOWN_COLOR}';
    $('v_chg_amt').style.color = up ? '{KLINE_UP_COLOR}' : '{KLINE_DOWN_COLOR}';
    if (v) {{
      $('v_vol').textContent = fmtVol(v.value);
      // 优先用真实成交额(交易所返回), fallback 为 vol * (h+l+c)/3 估算
      const realAmt = amtMap[d.time];
      const amt = realAmt ? realAmt.value : v.value * (d.high + d.low + d.close) / 3;
      $('v_amt').textContent = fmtAmt(amt);
    }}
  }}

  chart.subscribeCrosshairMove(param => {{
    if (!param.time) {{
      updateBar(RAW[RAW.length-1], RAW[RAW.length-2], VOL[VOL.length-1]);
      return;
    }}
    const t = typeof param.time === 'string' ? param.time :
      param.time.year+'-'+String(param.time.month).padStart(2,'0')+'-'+String(param.time.day).padStart(2,'0');
    updateBar(dataMap[t], (() => {{
      const idx = RAW.findIndex(x => x.time === t);
      return idx > 0 ? RAW[idx-1] : null;
    }})(), volMap[t]);
  }});

  // 初始化:显示最新一根
  updateBar(RAW[RAW.length-1], RAW[RAW.length-2], VOL[VOL.length-1]);

  // 周期切换(周/月聚合)
  function aggregate(period) {{
    if (period === 'day') return {{ candles: RAW, vols: VOL }};
    const groupKey = (date) => {{
      const d = new Date(date);
      if (period === 'week') {{
        const day = d.getDay() || 7;
        d.setDate(d.getDate() - day + 1);
        return d.toISOString().slice(0,10);
      }}
      if (period === 'month') return d.toISOString().slice(0,7) + '-01';
    }};
    const grouped = {{}};
    RAW.forEach(c => {{
      const k = groupKey(c.time);
      if (!grouped[k]) grouped[k] = {{ time: k, open: c.open, high: c.high, low: c.low, close: c.close }};
      else {{
        grouped[k].high = Math.max(grouped[k].high, c.high);
        grouped[k].low = Math.min(grouped[k].low, c.low);
        grouped[k].close = c.close;
      }}
    }});
    const volGroup = {{}};
    VOL.forEach(v => {{
      const k = groupKey(v.time);
      volGroup[k] = (volGroup[k]||0) + v.value;
    }});
    const newVols = Object.keys(volGroup).sort().map(k => ({{
      time: k, value: volGroup[k], color: grouped[k].close >= grouped[k].open ? '{KLINE_UP_COLOR}' : '{KLINE_DOWN_COLOR}'
    }}));
    const newCandles = Object.values(grouped).sort((a,b)=>a.time.localeCompare(b.time));
    return {{ candles: newCandles, vols: newVols }};
  }}

  function setPeriod(p) {{
    document.querySelectorAll('.btn').forEach(b => b.classList.remove('active'));
    const btn = document.getElementById('period_'+p);
    if (btn) btn.classList.add('active');
    const agg = aggregate(p);
    candleSeries.setData(agg.candles);
    volSeries.setData(agg.vols);
    // 均线:仅日K有MA
    Object.keys(maSeries).forEach(nd => {{
      maSeries[nd].setData(p === 'day' ? MA_DATA[nd] : []);
    }});
    chart.timeScale().fitContent();
  }}
  document.getElementById('period_day').onclick = () => setPeriod('day');
  document.getElementById('period_week').onclick = () => setPeriod('week');
  document.getElementById('period_month').onclick = () => setPeriod('month');
  document.getElementById('period_fit').onclick = () => chart.timeScale().fitContent();

  // 默认聚焦最近 N 根
  chart.timeScale().fitContent();
  if (RAW.length > {KLINE_VISIBLE_BARS}) {{
    const startTime = RAW[RAW.length - {KLINE_VISIBLE_BARS}].time;
    const lastTime = RAW[RAW.length-1].time;
    chart.timeScale().setVisibleRange({{ from: startTime, to: lastTime }});
  }}
}})();
</script>
</body>
</html>'''


def _kline_chart_html(kw_250, amount_series=None) -> str:
    """东财风格 K 线图(完整 HTML 字符串)

    组装器: 调 3 个子函数拼装最终 HTML
    """
    data = _prepare_chart_data(kw_250, amount_series)
    return _build_html_head() + _build_js_script(*data)