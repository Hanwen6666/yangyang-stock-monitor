"""
羊羊股市监测 - 业务健康检查 HTTP 服务

设计目标:
  - 独立端口 (8081)，不与 Streamlit 主服务耦合
  - 检查业务依赖: CloudBase API、本地数据、关键库
  - 返回结构化 JSON + HTTP 状态码 (200=健康, 503=不健康)
  - 轻量级，启动快（<100ms）

部署方式:
  - 由 systemd 单独启动 (yangyang-health.service)
  - 也可以与 streamlit 同进程（不推荐：耦合）
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

# ============================================================
# 配置
# ============================================================
PORT = int(os.environ.get("HEALTH_PORT", "8081"))
HOST = os.environ.get("HEALTH_HOST", "0.0.0.0")

# 项目根目录
PROJECT_ROOT = Path(__file__).parent

# 启动时间（用于计算 uptime）
START_TIME = time.time()

# 缓存：避免每次请求都重新加载检查结果（防 DDOS / 高频调用）
_CHECK_CACHE = {
    "data": None,
    "checked_at": 0,
    "ttl": 30,  # 缓存 30 秒
}


# ============================================================
# 各业务检查函数
# ============================================================

def _utc_iso() -> str:
    """返回 UTC ISO8601 时间戳（带时区后缀，方便日志聚合）"""
    return datetime.now(timezone.utc).isoformat()


def _local_iso() -> str:
    """返回本地时间 ISO8601"""
    return datetime.now().isoformat()


def check_process() -> dict:
    """进程自身健康（启动时间、内存等）"""
    try:
        import psutil
        proc = psutil.Process()
        uptime = time.time() - START_TIME
        mem = proc.memory_info()
        return {
            "status": "ok",
            "pid": proc.pid,
            "uptime_seconds": round(uptime, 1),
            "memory_mb": round(mem.rss / 1024 / 1024, 1),
            "cpu_percent": round(proc.cpu_percent(interval=0.1), 1),
            "threads": proc.num_threads(),
        }
    except ImportError:
        # psutil 不可用时的降级方案
        return {
            "status": "ok",
            "pid": os.getpid(),
            "uptime_seconds": round(time.time() - START_TIME, 1),
            "memory_mb": None,  # 拿不到内存信息
            "note": "psutil 未安装，跳过内存检查",
        }
    except Exception as e:
        return {
            "status": "warn",
            "error": str(e),
        }


def check_critical_libs() -> dict:
    """检查关键库是否能正常加载（业务可用性指标）"""
    critical = [
        "streamlit",
        "pandas",
        "numpy",
        "requests",
        "lib.constants",  # 项目内的常量模块
    ]
    results = {}
    overall_ok = True

    for lib_name in critical:
        try:
            __import__(lib_name)
            results[lib_name] = {"status": "ok"}
        except Exception as e:
            results[lib_name] = {
                "status": "fail",
                "error": f"{type(e).__name__}: {str(e)[:200]}",
            }
            overall_ok = False

    return {
        "status": "ok" if overall_ok else "fail",
        "libs": results,
    }


def check_data_files() -> dict:
    """检查关键数据文件是否存在 + 数据新鲜度"""
    checks = {}

    # 关键文件清单
    data_files = [
        ("etf_trend_history.csv", "ETF 强弱趋势历史数据", "dynamic"),  # 动态数据，应该每天更新
        ("etf_pool.csv", "ETF 池", "static"),                       # 静态池定义，不需要频繁更新
        ("a_stock_pool.csv", "A股股票池", "static"),                # 静态池定义，不需要频繁更新
    ]

    all_ok = True
    for filename, desc, kind in data_files:
        path = PROJECT_ROOT / "data" / filename
        if not path.exists():
            checks[filename] = {
                "status": "fail",
                "desc": desc,
                "kind": kind,
                "error": "文件不存在",
            }
            all_ok = False
            continue

        # 检查文件新鲜度
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        age_hours = (datetime.now() - mtime).total_seconds() / 3600

        if kind == "static":
            # 静态文件（股票池定义）：只检查存在性，不检查新鲜度
            status = "ok"
        elif age_hours > 168:  # 动态数据 7 天没更新 = fail
            status = "fail"
            all_ok = False
        elif age_hours > 48:   # 动态数据 2 天没更新 = warn
            status = "warn"
        else:
            status = "ok"

        checks[filename] = {
            "status": status,
            "desc": desc,
            "kind": kind,  # static / dynamic
            "size_bytes": path.stat().st_size,
            "modified": mtime.isoformat(),
            "age_hours": round(age_hours, 1),
        }

    # 检查 .asof 标记文件（数据"截止日期"标记）
    asof_path = PROJECT_ROOT / "data" / ".asof"
    if asof_path.exists():
        try:
            asof_date = asof_path.read_text().strip()
            checks[".asof"] = {
                "status": "ok",
                "value": asof_date,
                "desc": "数据截止日期",
            }
        except Exception as e:
            checks[".asof"] = {"status": "warn", "error": str(e)}
    else:
        checks[".asof"] = {"status": "warn", "error": ".asof 文件不存在"}

    return {
        "status": "ok" if all_ok else "fail",
        "files": checks,
    }


def check_cloudbase_api() -> dict:
    """检查 CloudBase API 连通性（带缓存 + 短超时）"""
    cache_key = "cloudbase"
    cached = _CHECK_CACHE.get(cache_key)
    if cached and (time.time() - cached["at"]) < _CHECK_CACHE["ttl"]:
        return cached["result"]

    try:
        import requests
        url = "https://agentchat-d0gsw7sn6c36f0b00.service.tcloudbase.com/api/etf-strength"
        # HEAD 请求只拿响应头，不下载完整 body
        resp = requests.head(url, timeout=5, allow_redirects=True)

        result = {
            "status": "ok" if resp.status_code < 400 else "warn",
            "url": url,
            "status_code": resp.status_code,
            "response_time_ms": round(resp.elapsed.total_seconds() * 1000, 1),
        }
        if resp.status_code >= 400:
            result["error"] = f"HTTP {resp.status_code}"

    except requests.Timeout:
        result = {
            "status": "fail",
            "error": "请求超时 (5s)",
            "url": "https://agentchat-d0gsw7sn6c36f0b00.service.tcloudbase.com",
        }
    except Exception as e:
        result = {
            "status": "fail",
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }

    # 写入缓存
    _CHECK_CACHE[cache_key] = {"at": time.time(), "result": result}
    return result


def check_disk() -> dict:
    """磁盘空间检查"""
    try:
        import shutil
        total, used, free = shutil.disk_usage("/")
        free_gb = free / (1024 ** 3)
        use_pct = (used / total) * 100

        if use_pct > 90:
            status = "fail"
        elif use_pct > 80:
            status = "warn"
        else:
            status = "ok"

        return {
            "status": status,
            "total_gb": round(total / (1024 ** 3), 1),
            "used_gb": round(used / (1024 ** 3), 1),
            "free_gb": round(free_gb, 1),
            "use_percent": round(use_pct, 1),
        }
    except Exception as e:
        return {"status": "warn", "error": str(e)}


# ============================================================
# 综合检查 + HTTP handler
# ============================================================

def run_all_checks() -> dict:
    """执行所有检查，汇总结果"""
    # 用线程并发跑，避免某个慢检查拖垮整体
    results = {}
    checkers = {
        "process": check_process,
        "libs": check_critical_libs,
        "data": check_data_files,
        "cloudbase_api": check_cloudbase_api,
        "disk": check_disk,
    }

    threads = []
    def _run(name, fn):
        try:
            results[name] = fn()
        except Exception as e:
            results[name] = {
                "status": "fail",
                "error": f"check crashed: {type(e).__name__}: {str(e)[:200]}",
                "traceback": traceback.format_exc()[-500:],
            }

    for name, fn in checkers.items():
        t = Thread(target=_run, args=(name, fn), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=10)  # 每个检查最多等 10 秒

    # 综合判定
    statuses = [r.get("status", "fail") for r in results.values()]
    if "fail" in statuses:
        overall = "fail"
        http_code = 503
    elif "warn" in statuses:
        overall = "warn"
        http_code = 200  # warn 仍算"可用"，HTTP 200，但响应里有 warning 标识
    else:
        overall = "ok"
        http_code = 200

    return {
        "overall": overall,
        "http_code": http_code,
        "checked_at": _local_iso(),
        "checked_at_utc": _utc_iso(),
        "checks": results,
    }


class HealthHandler(BaseHTTPRequestHandler):
    """HTTP 端点"""
    # 关闭默认 access log（避免日志刷屏）
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        now = time.time()

        # ========== /healthz 综合检查 ==========
        if self.path == "/healthz" or self.path == "/health":
            try:
                report = run_all_checks()
                code = report.pop("http_code")
                body = json.dumps(report, ensure_ascii=False, indent=2).encode()
                self._respond(code, "application/json; charset=utf-8", body)
            except Exception as e:
                err = {
                    "overall": "fail",
                    "error": f"{type(e).__name__}: {str(e)}",
                    "traceback": traceback.format_exc(),
                }
                self._respond(503, "application/json", json.dumps(err).encode())

        # ========== /health/live 简单存活（不依赖任何检查） ==========
        elif self.path == "/health/live":
            self._respond(200, "application/json", b'{"status":"alive"}')

        # ========== /health/ready 就绪（轻量检查：libs + process） ==========
        elif self.path == "/health/ready":
            try:
                report = {
                    "process": check_process(),
                    "libs": check_critical_libs(),
                }
                statuses = [r.get("status", "fail") for r in report.values()]
                ok = all(s != "fail" for s in statuses)
                code = 200 if ok else 503
                body = json.dumps({
                    "ready": ok,
                    "checks": report,
                }, ensure_ascii=False).encode()
                self._respond(code, "application/json; charset=utf-8", body)
            except Exception as e:
                self._respond(503, "application/json",
                              json.dumps({"ready": False, "error": str(e)}).encode())

        # ========== / 根路径给个提示 ==========
        elif self.path == "/":
            self._respond(200, "text/plain; charset=utf-8",
                          b"Yangyang Health Check Server\nEndpoints:\n"
                          b"  /healthz        - full check\n"
                          b"  /health/live    - liveness (always 200 if process alive)\n"
                          b"  /health/ready   - readiness (libs + process)\n")

        else:
            self._respond(404, "application/json", b'{"error":"not found"}')

    def _respond(self, code: int, content_type: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main():
    server = ThreadingHTTPServer((HOST, PORT), HealthHandler)
    print(f"[{_local_iso()}] health server listening on {HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n[{_local_iso()}] shutting down", flush=True)
        server.shutdown()


if __name__ == "__main__":
    main()