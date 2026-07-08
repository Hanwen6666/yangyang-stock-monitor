"""
ETF 强弱趋势算法 v27 — 从理财助理生产代码移植

源文件: /tmp/xysz/etf_score_v27.py (理财助理的 v27 真实生产代码)

⚠️ 不要修改阈值(已经过 20+ 次迭代调优)
"""
import math
import io
from datetime import datetime
import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter

try:
    import akshare
except ImportError:
    pass


# ============================================================
# 共享 HTTP Session — 复用 TCP 连接 / TLS 握手 / Keep-Alive
# 207 只 ETF 依次拉 K 线时能省 60% 以上网络时间
# ============================================================
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "yangyang-fetch/1.0"})
_SESSION.mount("https://", HTTPAdapter(pool_connections=30, pool_maxsize=30))
_SESSION.mount("http://", HTTPAdapter(pool_connections=20, pool_maxsize=20))


def _http_get(url, params=None, timeout=8, **kw):
    """封装 _SESSION.get,自动重试一次(网络抖动能拉起来)
    返回 (status_code, text/json-or-None)"""
    last_err = None
    for attempt in (0, 1):
        try:
            r = _SESSION.get(url, params=params, timeout=timeout, **kw)
            return r
        except requests.RequestException as e:
            last_err = e
            continue
    raise last_err


# ============================================================
# 核心算法(从 v27 移植,1:1)
# ============================================================
def slope_window(close, n):
    """几何平均年化斜率(单位%)。

    公式 = ( exp( log(c[-1]/c[0]) * 250/n ) - 1 ) * 100

    表示"如果最近 n 天的累计收益按复利年化后等效多少年化收益"。
    例如 n=20, 起点 1.000, 终点 1.020, 则年化 ≈ 1.020^12.5 - 1 ≈ 28.3%
    """
    if len(close) < n: return None
    c = close[-n:]
    if c[0] == 0:
        return None
    cum_log = math.log(c[-1] / c[0])
    return (math.exp(cum_log * 250 / n) - 1) * 100

def rolling_sharpe(close, n):
    """年化夏普"""
    if len(close) < n + 1: return None
    rets = np.diff(close[-n-1:]) / close[-n-1:-1]
    if len(rets) < 2 or np.std(rets) == 0: return None
    return (rets.mean() * 252) / (rets.std() * math.sqrt(252))

def adx_calc(close, high, low, n=14):
    """ADX(标准版) — numpy 加速版"""
    if len(close) < n + 1: return 0.0
    up = np.diff(high)
    up = np.insert(up, 0, 0)
    dn = -np.diff(low)
    dn = np.insert(dn, 0, 0)
    cond = (up > dn) & (up > 0)
    plus_dm = cond * up
    cond2 = (dn > up) & (dn > 0)
    minus_dm = cond2 * dn
    pc = np.roll(close, 1)
    pc[0] = close[0]
    tr = np.maximum(np.maximum(high - low, np.abs(high - pc)), np.abs(low - pc))
    atr_arr = pd.Series(tr).rolling(n, min_periods=1).mean().values
    pdm_arr = pd.Series(plus_dm).rolling(n, min_periods=1).mean().values
    mdm_arr = pd.Series(minus_dm).rolling(n, min_periods=1).mean().values
    atr_denom = np.where(atr_arr == 0, np.nan, atr_arr)
    plus_di = 100 * pdm_arr / atr_denom
    minus_di = 100 * mdm_arr / atr_denom
    sum_di = plus_di + minus_di
    dx_vals = np.divide(100 * np.abs(plus_di - minus_di), sum_di, out=np.full_like(sum_di, 0), where=sum_di != 0)
    return pd.Series(dx_vals).rolling(n, min_periods=1).mean().fillna(0).iloc[-1]
def classify_one(slope, slope_20, sc, adx_, up60):
    """6 档分类(v27 阈值)

    参数说明:
        slope: 主窗口年化斜率(slope_50, 基准)
        slope_20: 20日年化斜率(短趋势检测)
        sc: 综合夏普(0.2*sh20 + 0.5*sh50 + 0.3*sh120)
        adx_: ADX 趋势强度(>25强趋势, <20弱趋势)
        up60: 60日上涨比例

    阈值含义(v27 经验调优):
        - 超强势: slope>55(sc>2.0) + adx>28(趋势强劲) + 短斜不下跌
        - 横盘震荡: adx<12(无趋势) + |sc|<0.5(无倾向)
        - 一直下跌: slope<-12(持续下行) + sc<-0.8(负回报稳定)
        - 强势/震荡上涨: slope>10(上行趋势), 强弱由斜率+夏普细分
        - 震荡下跌: slope<-5(下行趋势) + sc<-0.5
    """
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
# K 线拉取 — 多源交叉验证
# ============================================================

def _parse_tencent_klines(code_tx):
    """腾讯源:返回 DataFrame 或 None"""
    try:
        r = _http_get(
            'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
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


def _parse_akshare(code_sina):
    """akshare 多接口试:新浪→东财(被限速时的fallback),返回 DataFrame 或 None"""
    code6 = code_sina[-6:] if len(code_sina) >= 6 else code_sina
    try:
        # 1) 先试新浪
        k = akshare.fund_etf_hist_sina(symbol=code6)
        if k is not None and len(k) > 100:
            rename = {"日期": "date", "开盘": "open", "收盘": "close",
                      "最高": "high", "最低": "low", "成交量": "volume"}
            return k.rename(columns=rename)
    except Exception:
        pass
    try:
        # 2) 新浪失败,试东方财富(含成交额)
        k = akshare.fund_etf_hist_em(symbol=code6, period="daily",
                                 start_date="20190101", end_date="20300101",
                                 adjust="qfq")
        if k is not None and len(k) > 100:
            rename = {"日期": "date", "开盘": "open", "收盘": "close",
                      "最高": "high", "最低": "low", "成交量": "volume",
                      "成交额": "amount"}
            df = k.rename(columns=rename)
            for c in ['open','close','high','low','volume','amount']:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors='coerce')
            return df[['date','open','close','high','low','volume','amount']]
    except Exception:
        pass
    return None


def _cross_validate(df_list):
    """多源交叉验证:比较最新 close 是否一致,返回最完整源"""
    valid = [(i, df) for i, df in enumerate(df_list) if df is not None and len(df) >= 100]
    if not valid:
        return None
    if len(valid) == 1:
        return valid[0][1]

    # 取长度最大的
    best_idx, best_df = max(valid, key=lambda x: len(x[1]))
    # 验证收盘价一致性(对比各源最后一天的 close)
    best_last = best_df['close'].astype(float).iloc[-1]
    for i, df in valid:
        if i == best_idx:
            continue
        other_last = df['close'].astype(float).iloc[-1]
        diff_pct = abs(best_last - other_last) / max(best_last, other_last) if max(best_last, other_last) > 0 else 1
        if diff_pct > 0.02:  # 超过 2% 偏差,打印警告
            print(f"[WARN] K线交叉验证偏差 {diff_pct*100:.2f}%: 源{best_idx} vs 源{i}")
    return best_df


_SQRT252 = math.sqrt(252)


def _sharpe_in_window(cc, n):
    rets = np.diff(cc[-n-1:]) / cc[-n-1:-1]
    if len(rets) < 2 or np.std(rets) == 0:
        return None
    return (rets.mean() * 252) / (rets.std() * _SQRT252)


def _compute_sliding_labels(close, high, low, n_windows=25):
    """批量计算 n 个滑动窗口(250天)的趋势分类

    优化: 一次性预计算 ADX 全段,避免 25 次重复计算。
    slope 和 sharpe 每窗口仍独立计算(开销极小)。
    返回 [最早, ..., 最新] 共 n_windows 个 label。
    """
    n_adx = 14
    s = pd.Series(close)
    h = pd.Series(high)
    l = pd.Series(low)
    up = h.diff()
    dn = -l.diff()
    plus_dm = ((up > dn) & (up > 0)) * up
    minus_dm = ((dn > up) & (dn > 0)) * dn
    pc = s.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr_n = tr.rolling(n_adx, min_periods=1).mean()
    plus_di = 100 * plus_dm.rolling(n_adx, min_periods=1).mean() / atr_n.replace(0, np.nan)
    minus_di = 100 * minus_dm.rolling(n_adx, min_periods=1).mean() / atr_n.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_arr = dx.rolling(n_adx, min_periods=1).mean().fillna(0).values

    n_total = len(close)
    if n_total < 250:
        # K 线不足 250 天:退回串行 calc_single_etf 处理
        _k = pd.DataFrame({'close': close, 'high': high, 'low': low})
        labels = []
        for offset in range(n_windows):
            end_idx = n_total - 1 - offset
            chunk = _k.iloc[max(0, end_idx - 249):end_idx + 1].reset_index(drop=True)
            _c = chunk['close'].astype(float).values
            _h = chunk['high'].astype(float).values if 'high' in chunk.columns else _c
            _l = chunk['low'].astype(float).values if 'low' in chunk.columns else _c
            # 直接内联再算
            _s50 = slope_window(_c, 50) or 0
            _s20 = slope_window(_c, 20) or 0
            # 注意: s120未被_classify_one使用,不计算
            _sh20 = rolling_sharpe(_c, 20)
            _sh50 = rolling_sharpe(_c, 50)
            _sh120 = rolling_sharpe(_c, 120)
            _sc = None
            if all(x is not None for x in [_sh20, _sh50, _sh120]):
                _sc = 0.2 * _sh20 + 0.5 * _sh50 + 0.3 * _sh120
            _a = adx_calc(_c, _h, _l)
            _rets = np.diff(_c) / _c[:-1]
            _up60 = (_rets[-60:] > 0).sum() / 60 if len(_rets) >= 60 else 0
            _lbl = classify_one(_s50, _s20, _sc or 0, _a, _up60)
            labels.append(_lbl)
        labels.reverse()
        return labels

    labels = []
    for offset in range(n_windows):
        end = n_total - offset
        start = max(0, end - 250)
        c = close[start:end]
        if len(c) < 20:
            labels.append('横盘震荡')
            continue

        # slope_window(close, 20/50)
        s50 = slope_window(c, 50) or 0
        s20 = slope_window(c, 20) or 0

        # rolling_sharpe(close, 20/50/120)
        sh20 = _sharpe_in_window(c, 20)
        sh50 = _sharpe_in_window(c, 50)
        sh120 = _sharpe_in_window(c, 120)
        if all(x is not None for x in [sh20, sh50, sh120]):
            sc = 0.20 * sh20 + 0.50 * sh50 + 0.30 * sh120
        else:
            sc = None

        # up_ratio
        rets = np.diff(c[-61:]) / c[-61:-1]
        up60 = (rets[-60:] > 0).sum() / 60 if len(rets) >= 60 else 0

        # ADX (取窗口最后一天,保护防止越界)
        adx_val = adx_arr[min(end - 1, len(adx_arr) - 1)]

        label = classify_one(s50, s20, sc or 0, adx_val, up60)
        labels.append(label)

    labels.reverse()  # 最旧 → 最新
    return labels


def fetch_kline(code6, min_len=250):
    """多源拉 K 线: 腾讯优先(最快), 失败才走 akshare+163 交叉验证

    优化:腾讯源在 Streamlit Cloud 上约 150ms,三源全拉 650ms+
    因此先快速试腾讯,够长直接返回;不够/失败才降级全源
    """
    code_sina = ('sh' if code6.startswith('5') or code6.startswith('1') else 'sz') + code6

    # 源1: 腾讯 https (最快且稳定) — 优先快速尝试
    first = _parse_tencent_klines(code_sina)
    if first is not None and len(first) >= min_len:
        return first

    # 快速失败:腾讯不够长,继续拉全源
    results = [first]

    # 源2: akshare (已模块级导入)
    results.append(_parse_akshare(code_sina))

    # 源3: 163 CSV API (不依赖第三方库)
    try:
        _163_prefix = "0" if code6 and code6[0] in "036" else "1"
        url = "https://quotes.money.163.com/service/chddata.html?code=" + _163_prefix + code6 + "&start=20200101&end=20300101"
        r = _SESSION.get(url, timeout=10,
                          headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and len(r.text) > 1000:
            import io
            df_sina = pd.read_csv(io.StringIO(r.text), encoding='gbk')
            if len(df_sina) >= 100 and '收盘价' in df_sina.columns:
                df_sina = df_sina.rename(columns={
                    '日期': 'date', '开盘价': 'open', '收盘价': 'close',
                    '最高价': 'high', '最低价': 'low', '成交量': 'volume',
                })
                df_sina['date'] = pd.to_datetime(df_sina['date']).dt.strftime('%Y-%m-%d')
                for c in ['open','close','high','low','volume']:
                    if c in df_sina.columns:
                        df_sina[c] = pd.to_numeric(df_sina[c], errors='coerce')
                df_sina = df_sina.dropna(subset=['close']).reset_index(drop=True)
                results.append(df_sina)
    except Exception:
        pass

    best = _cross_validate(results)
    if best is not None and len(best) > min_len:
        return best
    if best is not None and len(best) >= 100:
        return best
    return None


def fetch_kline_tencent(code6):
    """纯腾讯源(https)拉 K 线, 个股分析降级/备用使用"""
    code_tx = ('sh' if code6.startswith('5') or code6.startswith('1') else 'sz') + code6
    return _parse_tencent_klines(code_tx)


def _tencent_market_prefix(code6: str) -> str:
    """按股票代码推导腾讯快照的前缀(sh/sz)

    沪市: 6/9 开头（A股主板、 B 股）和 5 开头 (ETF/封闭式基金)
    深市: 0/1/2/3 开头（A股主板、中小板、创业板、 B 股）和 1/5 开头部分 ETF
    """
    if not code6:
        return 'sz'
    first = code6[0]
    # 沪市 ETF/基金/5xx: 5xx + 6xx + 9xx
    if first in '569':
        return 'sh'
    # 深市: 0xx (主板/中小板)、1xx/2xx (深市 ETF/ B 股)、3xx (创业板)
    return 'sz'


def _parse_amount_from_text(text: str):
    """腾讯快照 v_sh600519="1~贵州..." 解析成交额

    返回字段顺序 (价格/昨收/成交额)，仅取第 3 个数
    """
    import re
    if not text or '~' not in text:
        return None
    m = re.search(r'~([\d.]+)/([\d.]+)/([\d.]+)~', text)
    if not m:
        return None
    try:
        amount = float(m.group(3))
    except (ValueError, IndexError):
        return None
    # 腾讯有时返回 0（停牌/未开盘）
    return amount if amount > 0 else None


def fetch_amount(code6):
    """从腾讯快照取最新成交额(元)

    策略：
      1. 先按推导出的 sh/sz 前缀拉一次
      2. 拉不到/为 0，尝试相反前缀（代码归类模糊时的兜底）
      3. 都失败返回 None

    调用方可用 _cached_fetch_amount 包一层缓存
    """
    primary_prefix = _tencent_market_prefix(code6)
    fallbacks = [primary_prefix, 'sh' if primary_prefix == 'sz' else 'sz']

    for prefix in fallbacks:
        try:
            r = _SESSION.get(
                f'https://web.sqt.gtimg.cn/q={prefix}{code6}',
                timeout=5,
            )
            if r.status_code == 200:
                amount = _parse_amount_from_text(r.text)
                if amount is not None:
                    return amount
        except requests.RequestException:
            # 网络异常，换下一个前缀重试
            continue
        except Exception:
            continue
    return None


def calc_single_etf(kline, win_key="slope_50"):
    """根据 K 线计算一只 ETF 全部指标"""
    if kline is None or len(kline) < 250: return None
    k = kline.dropna(subset=['close']).reset_index(drop=True)
    if len(k) < 250: return None
    close = k['close'].astype(float).values
    high = k['high'].astype(float).values if 'high' in k.columns else close
    low = k['low'].astype(float).values if 'low' in k.columns else close

    s20 = slope_window(close, 20)
    s50 = slope_window(close, 50)
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
# CLI 验证入口（独立跑算法模块自检）
# ============================================================
if __name__ == "__main__":
    print("v27 algorithm module loaded")
    print(f"  slope_window: {slope_window(np.array([1,2,3,4,5.0]), 5)}")
    print(f"  classify_one sample: {classify_one(60, 20, 3.0, 30, 0.7)}")
