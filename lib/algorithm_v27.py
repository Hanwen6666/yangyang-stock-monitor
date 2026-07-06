#!/usr/bin/env python3
"""ETF v26 - 多窗口扫描: 40/45/50/55 斜率"""
import os, sys, time, math, warnings
from collections import Counter
import numpy as np
import pandas as pd
warnings.filterwarnings('ignore')
import akshare as ak
import requests

df = pd.read_csv('/home/ubuntu/.openclaw/workspace/etf_strong_weak/etf_final_207.csv')
df['代码'] = df['代码'].astype(str)
print(f'正式样本: {len(df)} 只', flush=True)

def fetch_kline(code6):
    code_sina = ('sh' if code6.startswith('5') or code6.startswith('1') else 'sz') + code6
    code_tx = code_sina
    try:
        k = ak.fund_etf_hist_sina(symbol=code_sina)
        if k is not None and len(k) > 250: return k, 'sina'
    except: pass
    try:
        r = requests.get('http://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
                         params={'param': f'{code_tx},day,,,640,qfq'}, timeout=8)
        if r.status_code == 200:
            data = r.json().get('data', {})
            if code_tx in data:
                klines = data[code_tx].get('qfqday') or data[code_tx].get('day')
                if klines and len(klines) > 250:
                    rows = [{'date':l[0],'open':float(l[1]),'close':float(l[2]),
                             'high':float(l[3]),'low':float(l[4]),'volume':float(l[5]) if len(l)>5 else 0}
                            for l in klines]
                    return pd.DataFrame(rows), 'tx'
    except: pass
    return None, None

def slope_window(close, n):
    if len(close) < n: return None
    c = close[-n:]
    # 用累计收益代替 polyfit 斜率, 避免 V 字型问题
    cum_log = math.log(c[-1] / c[0])
    return (math.exp(cum_log * 250 / n) - 1) * 100

def rolling_sharpe(close, n):
    if len(close) < n+1: return None
    rets = np.diff(close[-n-1:]) / close[-n-1:-1]
    if len(rets) < 2 or np.std(rets) == 0: return None
    return (rets.mean() * 252) / (rets.std() * math.sqrt(252))

def adx_calc(close, high, low, n=14):
    if len(close) < n+1: return 0
    s = pd.Series(close); h = pd.Series(high); l = pd.Series(low)
    up = h.diff(); dn = -l.diff()
    plus_dm = ((up > dn) & (up > 0)) * up
    minus_dm = ((dn > up) & (dn > 0)) * dn
    pc = s.shift(1)
    tr = pd.concat([h-l, (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    atr_n = tr.rolling(n, min_periods=1).mean()
    plus_di = 100 * plus_dm.rolling(n, min_periods=1).mean() / atr_n.replace(0, np.nan)
    minus_di = 100 * minus_dm.rolling(n, min_periods=1).mean() / atr_n.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.rolling(n, min_periods=1).mean().fillna(0).iloc[-1]

WEEK_INTERVAL = 5

print('>>> 拉 K 线 + 计算多窗口指标...', flush=True)
t1 = time.time()
records = []
for i, (_, row) in enumerate(df.iterrows()):
    code6 = str(row['代码'])
    k, src = fetch_kline(code6)
    if k is None or len(k) < 250: continue
    k = k.dropna(subset=['close']).reset_index(drop=True)
    if len(k) < 250: continue
    close = k['close'].astype(float).values
    high = k['high'].astype(float).values if 'high' in k.columns else close
    low = k['low'].astype(float).values if 'low' in k.columns else close
    
    series = []
    # 强制包含最后一个数据点 (避免步长漏掉最新几天)
    last_idx = len(close)
    for end_idx in list(range(250, last_idx, WEEK_INTERVAL)) + [last_idx]:
        if end_idx < 250: continue
        sub_close = close[:end_idx]
        sub_high = high[:end_idx]
        sub_low = low[:end_idx]
        
        # 多窗口斜率
        s20 = slope_window(sub_close, 20)
        s40 = slope_window(sub_close, 40)
        s45 = slope_window(sub_close, 45)
        s50 = slope_window(sub_close, 50)
        s55 = slope_window(sub_close, 55)
        s60 = slope_window(sub_close, 60)
        s120 = slope_window(sub_close, 120)
        
        # 多窗口夏普
        sh20 = rolling_sharpe(sub_close, 20)
        sh40 = rolling_sharpe(sub_close, 40)
        sh50 = rolling_sharpe(sub_close, 50)
        sh60 = rolling_sharpe(sub_close, 60)
        sh120 = rolling_sharpe(sub_close, 120)
        # 综合夏普: 短20% + 中50% + 长30%
        sc = None
        if all(x is not None for x in [sh20, sh50, sh120]):
            sc = 0.20*sh20 + 0.50*sh50 + 0.30*sh120
        
        a = adx_calc(sub_close, sub_high, sub_low)
        rets = np.diff(sub_close) / sub_close[:-1]
        up60 = (rets[-60:] > 0).sum() / 60
        
        series.append({
            'end_idx': end_idx,
            'slope_20': s20, 'slope_40': s40, 'slope_45': s45, 'slope_50': s50,
            'slope_55': s55, 'slope_60': s60, 'slope_120': s120,
            'sharpe_composite': sc,
            'adx': a, 'up_ratio_60': up60
        })
    
    if not series: continue
    latest = series[-1]
    records.append({
        '代码': code6, '名称': row['名称'], 'fund_size_yi': row['fund_size_yi'],
        'series': series, 'latest': latest
    })
    if (i+1) % 30 == 0:
        print(f'  [{i+1}/{len(df)}] 成功 {len(records)} 耗时 {time.time()-t1:.0f}s', flush=True)

print(f'完成 {len(records)} 耗时 {time.time()-t1:.0f}s', flush=True)

# === 无滞回分档 === 
def classify_multi(series, win_key):
    states = []
    for pt in series:
        slope = pt[win_key] or 0
        slope_20 = pt['slope_20'] or 0
        sc = pt['sharpe_composite'] or 0
        adx_ = pt['adx']
        up60 = pt['up_ratio_60']
        # 超强势
        if (slope > 55 and slope_20 > -10 and slope_20 > slope * 0.3 and sc > 2.0
            and adx_ > 28 and up60 > 0.62):
            cur = '超强势'
        elif adx_ < 12 and abs(sc) < 0.5:
            cur = '横盘震荡'
        elif slope < -12 and sc < -0.8:
            cur = '一直下跌'
        elif slope > 10 and sc > 0.3 and slope_20 > -10:
            if (slope > 20 and slope_20 > 5 and slope_20 > slope * 0.4
                and sc > 0.5 and adx_ > 15 and up60 > 0.50):
                cur = '强势'
            else:
                cur = '震荡上涨'
        elif slope < -5 and sc < -0.5:
            cur = '震荡下跌'
        elif slope < 0 and sc < 0:
            cur = '震荡下跌'
        else:
            cur = '横盘震荡'
        states.append(cur)
    return states

print('\n========== 多窗口分布对比 ==========', flush=True)
results_by_window = {}
for win in ['slope_40', 'slope_45', 'slope_50', 'slope_55', 'slope_60']:
    print(f'\n--- 主窗口: {win} ---', flush=True)
    cats = []
    for r in records:
        states = classify_multi(r['series'], win)
        cats.append(states[-1])
    dist = Counter(cats)
    order = ['超强势','强势','震荡上涨','横盘震荡','震荡下跌','一直下跌']
    for cat in order:
        print(f'  {cat}: {dist.get(cat, 0)}', flush=True)
    results_by_window[win] = dist

# 应用最佳窗口 (默认 50)
from collections import Counter

WIN = 'slope_50'
records_final = []
for r in records:
    states = classify_multi(r['series'], WIN)
    cur_cat = states[-1]
    n_changes = sum(1 for i in range(1, len(states)) if states[i] != states[i-1])
    rec = dict(r['latest'])
    rec['代码'] = r['代码']
    rec['名称'] = r['名称']
    rec['fund_size_yi'] = r['fund_size_yi']
    rec['category'] = cur_cat
    rec['n_changes'] = n_changes
    rec['n_points'] = len(states)
    records_final.append(rec)

df_final = pd.DataFrame(records_final)

import re
THEME_MAP = {
    '半导体|芯片|集成电路|电子|信息技术|科技|AI|人工智能|机器人|智能制造|软件|互联网|游戏|传媒|通信|云计算|数字': '科技',
    '医药|医疗|创新药|生物|疫苗|中药|医药卫生|医疗器械|健康': '医药健康',
    '消费|食品|饮料|白酒|家电|零售|农牧|养殖|美容|化妆': '消费',
    '金融|银行|证券|保险|券商|金融科技': '金融',
    '地产|建筑|建材|基建|工程|机械|钢铁|煤炭|有色|化工|石油|能源|电力|公用|环保|碳|资源|电网': '周期资源',
    '军工|国防|航空|航天|船舶': '军工',
    '红利|低波|高股息|分红|价值|现金流': '红利价值',
    '新能源|光伏|锂电|电池|新能源车|电车|风电|储能|碳中和|智能驾驶|低碳': '新能源',
    '港股|恒生|港股通|恒生科技|H股|中概': '港股',
    '纳指|纳斯达克|标普|美国|日经|东证|德国|欧洲|海外|沙特|越南|印度|全球|东南亚': '海外',
    '上证|沪深300|中证500|中证1000|中证2000|A50|A500|中证800|科创50|科创100|科创200|创业板|科创板|双创|国证2000': '宽基',
    '债|国债|地方债|政金|信用债|短融|可转债|城投': '债券',
    '黄金|白银|贵金属': '贵金属',
    '物流|快递|交通运输|航运': '物流运输',
}
def theme_of(name):
    for pat, t in THEME_MAP.items():
        if re.search(pat, name): return t
    return '其他'
df_final['theme'] = df_final['名称'].apply(theme_of)

cat_order = {'超强势':0,'强势':1,'震荡上涨':2,'横盘震荡':3,'震荡下跌':4,'一直下跌':5}
df_final['_o'] = df_final['category'].map(cat_order)
df_final = df_final.sort_values(['_o','fund_size_yi'], ascending=[True,False]).drop(columns=['_o'])

print('\n========== v26 最终版 (主窗口 50) ==========', flush=True)
print(df_final['category'].value_counts().reindex(
    ['超强势','强势','震荡上涨','横盘震荡','震荡下跌','一直下跌'], fill_value=0).to_string(), flush=True)

print('\n========== 主题 × 档位 ==========', flush=True)
print(pd.crosstab(df_final['theme'], df_final['category']).reindex(
    columns=['超强势','强势','震荡上涨','横盘震荡','震荡下跌','一直下跌'], fill_value=0).to_string(), flush=True)

for cat in ['超强势','强势','震荡上涨','横盘震荡','震荡下跌','一直下跌']:
    print(f'\n========== {cat} ==========', flush=True)
    sub = df_final[df_final['category']==cat].nlargest(15, 'fund_size_yi')
    print(sub[['代码','名称','fund_size_yi','slope_20','slope_50','slope_120',
              'sharpe_composite','adx','up_ratio_60','n_changes','theme']].to_string(index=False), flush=True)

df_final.to_csv('/home/ubuntu/.openclaw/workspace/etf_strong_weak/etf_strong_weak_207_v26.csv', index=False)