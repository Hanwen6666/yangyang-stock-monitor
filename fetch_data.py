"""
从 CloudBase API 抓取最新数据,归档到 data/
理财助理更新 CSV 后,运行本脚本即可刷新 Streamlit 上的数据。

可作为脚本运行:
  python fetch_data.py
  python fetch_data.py --base https://your-env.service.tcloudbase.com/api/etf-strength

也可作为模块被 app.py 调用:
  from fetch_data import refresh_data
  refresh_data()  # → 返回 dict 供前端展示
"""
import argparse
import csv
import json
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

DEFAULT_BASE = "https://agentchat-d0gsw7sn6c36f0b00.service.tcloudbase.com/api/etf-strength"


def fetch(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "yangyang-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def refresh_data(base_url: str = DEFAULT_BASE, timeout: int = 20) -> dict:
    """从 API 拉最新数据,重写本地 CSV。

    Returns:
        dict {
            "ok": bool,
            "asof_date": str,
            "n_etfs": int,
            "n_points": int,
            "fetched_at": str (ISO),
            "elapsed_ms": int,
            "error": str | None,
        }
    """
    t0 = datetime.now()
    try:
        list_data = fetch(f"{base_url}/list?top_n=500", timeout)
        hist_data = fetch(f"{base_url}/trend-history", timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
        return {
            "ok": False, "error": str(e), "fetched_at": t0.isoformat(timespec="seconds"),
            "asof_date": None, "n_etfs": 0, "n_points": 0, "elapsed_ms": 0,
        }

    items = list_data.get("items", [])
    asof = list_data.get("asof_date", "")

    # results.csv
    cols = ["code", "name", "category", "strength_label", "fund_size_yi",
            "slope_20", "slope_50", "slope_120", "sharpe_composite", "adx",
            "up_ratio_60", "n_changes", "n_points", "asof_date"]
    write_csv(DATA_DIR / "results.csv",
              [{**it, "asof_date": asof} for it in items], cols)

    # trend_history.csv
    points = hist_data.get("points", [])
    hist_rows = []
    for it in hist_data.get("items", []):
        row = {"code": it["code"], "name": it["name"]}
        for p, v in zip(points, it.get("history", [])):
            row[p] = v
        hist_rows.append(row)
    write_csv(DATA_DIR / "etf_trend_history.csv", hist_rows, ["code", "name"] + points)

    # asof 标记
    (DATA_DIR / ".asof").write_text(asof, encoding="utf-8")

    elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
    return {
        "ok": True,
        "asof_date": asof,
        "n_etfs": len(items),
        "n_points": len(points),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_ms": elapsed_ms,
        "error": None,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default=DEFAULT_BASE, help="API base URL")
    args = p.parse_args()
    res = refresh_data(args.base)
    if res["ok"]:
        print(f"✅ {res['n_etfs']} ETFs (asof={res['asof_date']}) · "
              f"{res['n_points']} points · {res['elapsed_ms']}ms · "
              f"fetched at {res['fetched_at']}")
    else:
        print(f"❌ {res['error']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()