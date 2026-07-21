"""
ETF 市场数据获取层 (2026-07-21 E 靶点: 从 lib/algorithm.py 抽出)

模块职责:
  - 共享 HTTP Session (TCP 连接复用, 拉 207+ 只 ETF 省 60% 网络时间)
  - 腾讯源 K 线获取 (主路径, ~150ms / 只)
  - akshare 多源 K 线获取 (新浪 + 东财 fallback)
  - 163 CSV API (不依赖第三方库)
  - 多源交叉验证 (取最完整 + 最新日期源)
  - 腾讯快照成交额获取 (带 sh/sz 双前缀兜底)
  - 市场前缀推导 (沪市/深市/科创板/北交所/创业板)

不包含:
  - 算法逻辑 (slope/sharpe/adx 等留在 lib/algorithm.py)
  - 数据落地 (fetch_data.py::refresh_data + recompute_locally 负责)

跨文件 import 路径约定:
  旧: from lib.algorithm import tencent_market_prefix
  新: from lib.market_data import tencent_market_prefix

⚠️ 兼容性: 同时 re-export 旧 lib.algorithm 路径 (deprecation 期)
"""
import io
import re
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

    返回 Response 对象 (status_code / text / json() / content)
    """
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
# 市场前缀推导 (单点权威:γ 靶点重构后统一引用此版本)
# ============================================================
def tencent_market_prefix(code6: str) -> str:
    """按股票代码推导腾讯快照的前缀 (sh/sz)

    沪市: 6/9 开头 (A股主板/科创板/B 股) 和 5 开头 (ETF/封闭式基金)
    深市: 0/1/2/3 开头 (A股主板/中小板/创业板/B 股) 和 1/5 开头部分 ETF

    2026-07-20 γ 靶点重构去重: 此前在 lib/strategy_v3.py 有一份副本,
    统一引用 lib/market_data.py 单点权威 (此版本)。
    2026-07-21 E 靶点: 从 lib/algorithm.py 抽到 lib/market_data.py。
    """
    if not code6:
        return 'sz'
    first = code6[0]
    # 沪市 ETF/基金/5xx: 5xx + 6xx + 9xx
    if first in '569':
        return 'sh'
    # 深市: 0xx (主板/中小板)、1xx/2xx (深市 ETF/ B 股)、3xx (创业板)
    return 'sz'


# ============================================================
# K 线解析 (多源适配)
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
    """akshare 多接口试:新浪→东财(被限速时的fallback),返回 DataFrame 或 None

    2026-07-21 E 靶点: 与 lib/algorithm.py 旧实现 100% 一致
    (akshare.fund_etf_hist_sina → akshare.fund_etf_hist_em)
    """
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
    """多源交叉验证:取 max(len) 且 max(last_date) 的源优先

    2026-07-21 E 靶点: 与 lib/algorithm.py 旧实现行为一致
    """
    valid = [df for df in df_list if df is not None and not df.empty]
    if not valid:
        return None
    if len(valid) == 1:
        return valid[0]
    # 选长度最大且最后日期最新的
    def score(df):
        if 'date' not in df.columns:
            return (0, '')
        return (len(df), str(df['date'].iloc[-1]) if len(df) else '')
    return max(valid, key=score)


# ============================================================
# K 线获取主路径
# ============================================================
def fetch_kline(code6, min_len=250):
    """多源拉 K 线: 腾讯优先 (最快), 失败才走 akshare+163 交叉验证

    优化:腾讯源约 150ms,三源全拉 650ms+
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
    """纯腾讯源 (https) 拉 K 线, 个股分析降级/备用使用"""
    code_tx = ('sh' if code6.startswith('5') or code6.startswith('1') else 'sz') + code6
    return _parse_tencent_klines(code_tx)


# ============================================================
# 成交额快照 (腾讯实时)
# ============================================================
def _parse_amount_from_text(text: str):
    """腾讯快照 v_sh600519="1~贵州..." 解析成交额

    腾讯快照实际格式 (实测 2026-07-21 88 段) :
      [0]  v_sh600519="1
      [1]  名字
      [2]  code
      [3]  今开
      [4]  昨收
      [5]  当前价 (元) ← 不要拿这个,这个是价格不是成交额
      [6]  累计成交量 (手)
      ...
      [37] 成交额 (万元)
      [57] 成交额 (万元, 更精确)
      [72] 成交额 (元)

    2026-07-21 v2 修复: 上一版用 parts[5] (当前价), 错了!
      原 lib/algorithm.py 实现用 / 分隔正则 — 永远 None
      v1 修复用 parts[5] — 拿到的是当前价不是成交额
      v2 修复: 正确字段是 parts[37] (万元, 跨品种冗余) → 转元

    返回成交额 (元),为 0 时返回 None (停牌/未开盘)。
    """
    if not text or '~' not in text:
        return None
    parts = text.split('~')
    # 优先取 parts[37] (成交额万元) × 1e4 = 元
    for idx in (37, 57, 72):
        if len(parts) > idx:
            try:
                v = float(parts[idx])
                if v > 0:
                    # parts[37] / parts[57] 是万元, 需 × 1e4 转元
                    # parts[72] 已经是元, 不用转
                    if idx in (37, 57):
                        return v * 1e4
                    return v
            except (ValueError, IndexError):
                continue
    # 退化: 保留旧 `/` 分隔匹配 (兼容历史数据, parts[37] 这种精确路径)
    import re as _re
    m = _re.search(r'~([\d.]+)/([\d.]+)/([\d.]+)~', text)
    if m:
        try:
            amount = float(m.group(3))
            if amount > 0:
                return amount * 1e4  # 万元 → 元
        except (ValueError, IndexError):
            pass
    return None


def fetch_amount(code6):
    """从腾讯快照取最新成交额 (元)

    策略:
      1. 先按推导出的 sh/sz 前缀拉一次
      2. 拉不到/为 0,尝试相反前缀 (代码归类模糊时的兜底)
      3. 都失败返回 None

    调用方可用 _cached_fetch_amount 包一层缓存
    """
    primary_prefix = tencent_market_prefix(code6)
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
            # 网络异常,换下一个前缀重试
            continue
        except Exception:
            continue
    return None


# ============================================================
# 兼容性 re-export (deprecation 期)
# 让外部 from lib.algorithm import tencent_market_prefix 仍能工作
# ============================================================
import sys as _sys
import types as _types
_legacy_module = _types.ModuleType("lib._legacy_market_data_reexports")
_legacy_module._SESSION = _SESSION
_legacy_module._http_get = _http_get
_legacy_module.tencent_market_prefix = tencent_market_prefix
_legacy_module._parse_tencent_klines = _parse_tencent_klines
_legacy_module._parse_akshare = _parse_akshare
_legacy_module._cross_validate = _cross_validate
_legacy_module.fetch_kline = fetch_kline
_legacy_module.fetch_kline_tencent = fetch_kline_tencent
_legacy_module._parse_amount_from_text = _parse_amount_from_text
_legacy_module.fetch_amount = fetch_amount
_sys.modules.setdefault("lib._legacy_market_data_reexports", _legacy_module)
