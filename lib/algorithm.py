"""
ETF 强弱趋势算法 v27 — 从理财助理生产代码移植

源文件: /tmp/xysz/etf_score_v27.py (理财助理的 v27 真实生产代码)

⚠️ 不要修改阈值(已经过 20+ 次迭代调优)
"""
import math
import numpy as np
import pandas as pd
import requests


# ============================================================
# 核心算法(从 v27 移植,1:1)
# ============================================================
def slope_window(close, n):
    """年化斜率:(exp(log(c[-1]/c[0]) * 250/n) - 1) * 100"""
    if len(close) < n: return None
    c = close[-n:]
    cum_log = math.log(c[-1] / c[0])
    return (math.exp(cum_log * 250 / n) - 1) * 100

def rolling_sharpe(close, n):
    """年化夏普"""
    if len(close) < n + 1: return None
    rets = np.diff(close[-n-1:]) / close[-n-1:-1]
    if len(rets) < 2 or np.std(rets) == 0: return None
    return (rets.mean() * 252) / (rets.std() * math.sqrt(252))

def adx_calc(close, high, low, n=14):
    """ADX(标准版)"""
    if len(close) < n + 1: return 0.0
    s = pd.Series(close); h = pd.Series(high); l = pd.Series(low)
    up = h.diff(); dn = -l.diff()
    plus_dm = ((up > dn) & (up > 0)) * up
    minus_dm = ((dn > up) & (dn > 0)) * dn
    pc = s.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr_n = tr.rolling(n, min_periods=1).mean()
    plus_di = 100 * plus_dm.rolling(n, min_periods=1).mean() / atr_n.replace(0, np.nan)
    minus_di = 100 * minus_dm.rolling(n, min_periods=1).mean() / atr_n.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.rolling(n, min_periods=1).mean().fillna(0).iloc[-1]

def classify_one(slope, slope_20, sc, adx_, up60):
    """6 档分类(v27 阈值)"""
    if (slope > 55 and slope_20 > -10 and slope_20 > slope * 0.3
            and sc > 2.0 and adx_ > 28 and up60 > 0.62):
        return "超强势"
    if adx_ < 12 and abs(sc) < 0.5:
        return "横盘震荡"
    if slope < -12 and sc < -0.8:
        return "一直下跌"
    if slope > 10 and sc > 0.3 and slope_20 > -10:
        if (slope > 20 and slope_20 > 5 and slope_20 > slope * 0.4
                and sc > 0.5 and adx_ > 15 and up60 > 0.50):
            return "强势"
        return "震荡上涨"
    if slope < -5 and sc < -0.5:
        return "震荡下跌"
    if slope < 0 and sc < 0:
        return "震荡下跌"
    return "横盘震荡"

# ============================================================
# K 线拉取(从 v27 移植)
# ============================================================
def fetch_kline(code6, min_len=250):
    """从 sina/腾讯 拉 K 线"""
    code_sina = ('sh' if code6.startswith('5') or code6.startswith('1') else 'sz') + code6
    code_tx = code_sina
    try:
        import akshare as ak
        k = ak.fund_etf_hist_sina(symbol=code_sina)
        if k is not None and len(k) > min_len:
            rename = {"日期": "date", "开盘": "open", "收盘": "close",
                      "最高": "high", "最低": "low", "成交量": "volume"}
            return k.rename(columns=rename)
    except Exception:
        pass
    try:
        r = requests.get(
            'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
            params={'param': f'{code_tx},day,,,640,qfq'}, timeout=8,
        )
        if r.status_code == 200:
            data = r.json().get('data', {})
            if code_tx in data:
                klines = data[code_tx].get('qfqday') or data[code_tx].get('day')
                if klines and len(klines) > min_len:
                    rows = [
                        {'date': l[0], 'open': float(l[1]), 'close': float(l[2]),
                         'high': float(l[3]), 'low': float(l[4]),
                         'volume': float(l[5]) if len(l) > 5 else 0}
                        for l in klines
                    ]
                    return pd.DataFrame(rows)
    except Exception:
        pass
    return None


def fetch_kline_tencent(code6):
    """纯腾讯源拉 K 线(不依赖 akshare),供个股分析降级使用"""
    code_tx = ('sh' if code6.startswith('5') or code6.startswith('1') else 'sz') + code6
    try:
        r = requests.get(
            'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
            params={'param': f'{code_tx},day,,,640,qfq'}, timeout=8,
        )
        if r.status_code == 200:
            data = r.json().get('data', {})
            if code_tx in data:
                klines = data[code_tx].get('qfqday') or data[code_tx].get('day')
                if klines:
                    rows = [
                        {'date': l[0], 'open': float(l[1]), 'close': float(l[2]),
                         'high': float(l[3]), 'low': float(l[4]),
                         'volume': float(l[5]) if len(l) > 5 else 0}
                        for l in klines
                    ]
                    return pd.DataFrame(rows)
    except Exception:
        pass
    return None


# ============================================================
# 单只 ETF 完整计算(v27 移植)
# ============================================================
def calc_single_etf(kline, win_key="slope_50"):
    """根据 K 线计算一只 ETF 全部指标"""
    if kline is None or len(kline) < 250: return None
    k = kline.dropna(subset=['close']).reset_index(drop=True)
    if len(k) < 250: return None
    close = k['close'].astype(float).values
    high = k['high'].astype(float).values if 'high' in k.columns else close
    low = k['low'].astype(float).values if 'low' in k.columns else close

    s20 = slope_window(close, 20)
    s40 = slope_window(close, 40)
    s45 = slope_window(close, 45)
    s50 = slope_window(close, 50)
    s55 = slope_window(close, 55)
    s60 = slope_window(close, 60)
    s120 = slope_window(close, 120)

    sh20 = rolling_sharpe(close, 20)
    sh50 = rolling_sharpe(close, 50)
    sh120 = rolling_sharpe(close, 120)
    sc = None
    if all(x is not None for x in [sh20, sh50, sh120]):
        sc = 0.20 * sh20 + 0.50 * sh50 + 0.30 * sh120

    a = adx_calc(close, high, low)
    rets = np.diff(close) / close[:-1]
    up60 = (rets[-60:] > 0).sum() / 60

    # 6 档分类(用主窗口 slope_50)
    label = classify_one(s50 or 0, s20 or 0, sc or 0, a, up60)

    return {
        'slope_20': round(s20, 4) if s20 is not None else 0,
        'slope_50': round(s50, 4) if s50 is not None else 0,
        'slope_120': round(s120, 4) if s120 is not None else 0,
        'sharpe_composite': round(sc, 3) if sc is not None else 0,
        'adx': round(a, 2),
        'up_ratio_60': round(up60, 6),
        'strength_label': label,
        'n_changes': int((rets[-60:] != 0).sum()),
        'n_points': int((close[-60:] != 0).sum()),
    }

# ============================================================
# 批量:拉 K 线 + 算指标
# ============================================================
def compute_all_metrics(codes, progress_callback=None, min_len=250):
    """codes: List[str] 6 位代码

    Returns: DataFrame[code, slope_20, slope_50, slope_120,
                        sharpe_composite, adx, up_ratio_60,
                        strength_label, n_changes, n_points]
    """
    rows = []
    for i, code in enumerate(codes):
        k = fetch_kline(code, min_len)
        if k is None:
            if progress_callback: progress_callback(i+1, len(codes), code, None, "no kline")
            continue
        m = calc_single_etf(k)
        if m is None:
            if progress_callback: progress_callback(i+1, len(codes), code, None, "data insufficient")
            continue
        row = {'code': code, **m}
        rows.append(row)
        if progress_callback: progress_callback(i+1, len(codes), code, m, "ok")
    return pd.DataFrame(rows)

# ============================================================
# 验证:用现有 v27 已知数据反算应该一致
# ============================================================
if __name__ == "__main__":
    print("v27 algorithm module loaded")
    print(f"  slope_window exists: {slope_window(np.array([1,2,3,4,5.0]), 5)}")
    print(f"  classify_one(super strong sample): {classify_one(60, 20, 3.0, 30, 0.7)}")
