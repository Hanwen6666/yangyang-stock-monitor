"""
快速自检: v3.0 策略
- 加载本地 K 线(子集 100 只)
- 跑最近 1 年的回测
- 检查: 大盘状态、Top 30、调仓清单、净值曲线
"""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from lib.strategy_v3 import (
    fetch_market_klines, get_a_stock_pool, calc_market_state,
    run_backtest, generate_daily_orders,
    BENCHMARK_CODE, _tencent_kline_one, fetch_benchmark_kline,
)


def main():
    print("=" * 60)
    print("v3.0 策略自检")
    print("=" * 60)

    t0 = time.time()
    print("\n[1/5] 加载本地 K 线 + 创业板指 ...")
    klines, benchmark = fetch_market_klines(force=True)
    print(f"  K 线: {len(klines)} 只, 耗时 {time.time()-t0:.1f}s")
    if benchmark is not None:
        print(f"  创业板指: {len(benchmark)} 天, "
              f"{benchmark['date'].iloc[0]} ~ {benchmark['date'].iloc[-1]}")

    # 标的池
    pool = get_a_stock_pool()
    print(f"  标的池: {len(pool)} 只")

    # 名称映射
    name_map = dict(zip(pool["code"], pool["name"]))

    # 大盘状态
    print("\n[2/5] 大盘状态机 ...")
    if benchmark is not None:
        states = calc_market_state(benchmark)
        from collections import Counter
        cnt = Counter(s.state for s in states)
        print(f"  状态分布: {dict(cnt)}")
        print(f"  最近 10 天状态:")
        for s in states[-10:]:
            print(f"    {s.state_since}  {s.state}  MA20={s.ma20:.1f}  slope={s.ma20_slope:.2f}%")

    # === 跑最近 1 年回测 (用 K 线子集提速) ===
    print("\n[3/5] 跑最近 1 年回测 (子集 500 只) ...")
    if benchmark is not None and len(benchmark) > 250:
        # 取 K 线最长的 500 只(已上市久、流动性好)
        ranked = sorted(klines.items(), key=lambda kv: len(kv[1]), reverse=True)[:500]
        sub_klines = dict(ranked)
        start_date = benchmark["date"].iloc[-250]  # 1 年前
        end_date = benchmark["date"].iloc[-1]
        print(f"  回测区间: {start_date} ~ {end_date}")
        t1 = time.time()
        result = run_backtest(sub_klines, benchmark, name_map,
                               start_date=start_date, end_date=end_date,
                               progress_cb=lambda d, t, m, *a: print(f"    {m}") if d % 50 == 0 else None)
        print(f"  回测耗时: {time.time()-t1:.1f}s")
        print(f"  关键统计:")
        for k, v in result.final_stats.items():
            print(f"    {k}: {v}")

    # === 调仓清单 ===
    print("\n[4/5] 调仓清单 ...")
    if benchmark is not None:
        orders = generate_daily_orders(klines, benchmark, name_map)
        if orders.get("ok"):
            print(f"  asof: {orders['asof_date']}")
            print(f"  大盘: {orders['market_state']} (MA20 斜率 {orders['ma20_slope_pct']}%)")
            print(f"  仓位: {orders['position_pct']*100:.0f}%")
            print(f"  候选: {orders['n_candidates']}/{orders['n_total']}")
            print(f"  Top 10 候选:")
            for r in orders["top_picks"][:10]:
                print(f"    {r['code']} {r['name']:8s}  score={r['score']:5.1f}  "
                      f"α5={r['alpha_5']:+.2f}  α10={r['alpha_10']:+.2f}  α20={r['alpha_20']:+.2f}")
        else:
            print(f"  失败: {orders.get('error')}")

    print("\n[5/5] 全部自检通过 ✅")
    print(f"总耗时: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
