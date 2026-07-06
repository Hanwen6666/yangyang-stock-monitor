"""
从 CloudBase API 抓取最新数据,归档到 data/
理财助理更新 CSV 后,运行本脚本即可刷新 Streamlit 上的数据。

用法:
  python fetch_data.py
  python fetch_data.py --base https://your-env.service.tcloudbase.com/api/etf-strength
"""
import argparse
import csv
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

DEFAULT_BASE = "https://agentchat-d0gsw7sn6c36f0b00.service.tcloudbase.com/api/etf-strength"


def fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "yangyang-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default=DEFAULT_BASE, help="API base URL")
    args = p.parse_args()

    print(f"[{datetime.now():%H:%M:%S}] fetching from {args.base} ...")

    list_data = fetch(f"{args.base}/list?top_n=500")
    hist_data = fetch(f"{args.base}/trend-history")

    items = list_data.get("items", [])
    print(f"  list: {len(items)} ETFs (asof={list_data.get('asof_date')})")

    # results.csv
    cols = ["code", "name", "category", "strength_label", "fund_size_yi",
            "slope_20", "slope_50", "slope_120", "sharpe_composite", "adx",
            "up_ratio_60", "n_changes", "n_points", "asof_date"]
    write_csv(DATA_DIR / "results.csv", [
        {**it, "asof_date": list_data.get("asof_date", "")} for it in items
    ], cols)

    # trend_history.csv
    points = hist_data.get("points", [])
    hist_rows = []
    for it in hist_data.get("items", []):
        row = {"code": it["code"], "name": it["name"]}
        for p, v in zip(points, it.get("history", [])):
            row[p] = v
        hist_rows.append(row)
    write_csv(DATA_DIR / "etf_trend_history.csv", hist_rows, ["code", "name"] + points)

    # 写 asof 标记文件(mtime 用于显示"归档时间")
    (DATA_DIR / ".asof").write_text(list_data.get("asof_date", ""), encoding="utf-8")

    print(f"  trend: {len(hist_rows)} rows × {len(points)} points")
    print(f"  saved to:")
    print(f"    {DATA_DIR / 'results.csv'}")
    print(f"    {DATA_DIR / 'etf_trend_history.csv'}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)