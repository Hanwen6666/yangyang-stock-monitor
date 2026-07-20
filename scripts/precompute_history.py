"""
预生成趋势演变 HTML 矩阵并保存为静态文件

执行时机: 每次数据刷新完成后, 或者 cron 每小时跑一次
输出: data/history_matrix_table.html
"""
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from tabs import etf_strength as tab

DATA_DIR = Path("/opt/yangyang-stock-monitor/data")
OUTPUT = DATA_DIR / "history_matrix_table.html"


def _call_build_html(df_hist, df_res, n_days: int = 25):
    """🔧 修复: 之前调用 tab._precompute_history_matrix(),但该函数不存在
    实际是 _build_history_html(df_hist, points_tuple, df_res,
                                 selected_dates_tuple, label_filter_list)

    这里做一层包装,统一入参风格。
    """
    points = [c for c in df_hist.columns if c not in ("code", "name")]
    if not points:
        return None, 0, 0

    # 取最近 n_days 个交易日 (points 是从远→近 排列, 反转后取末尾 n_days)
    selected = list(reversed(points))[:min(n_days, len(points))]

    return tab._build_history_html(
        df_hist,
        tuple(points),
        df_res,
        tuple(selected),
        [],
    )


def main():
    t0 = datetime.now()
    print(f"[{t0}] precompute started")

    # 读 CSV
    res_p = DATA_DIR / "results.csv"
    hist_p = DATA_DIR / "etf_trend_history.csv"
    if not res_p.exists() or not hist_p.exists():
        print("CSV 缺失, 跳过")
        return
    df_res = pd.read_csv(res_p)
    df_hist = pd.read_csv(hist_p)
    if df_res.empty or df_hist.empty:
        print("数据为空, 跳过")
        return

    # 拼 HTML (从 etf_strength 模块复用)
    html_body, n_etf, n_days = _call_build_html(df_hist, df_res, n_days=25)
    if html_body is None:
        print("无趋势数据点, 跳过")
        return

    # 只输出 <table> 主体部分, 配合 streamlit st.markdown
    OUTPUT.write_text(html_body, encoding="utf-8")
    elapsed = int((datetime.now() - t0).total_seconds() * 1000)
    print(f"[done] 写入 {OUTPUT} ({OUTPUT.stat().st_size} 字节, {elapsed}ms)")


if __name__ == "__main__":
    main()