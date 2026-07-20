"""
安全的本地文件 IO 工具
  - atomic_write_text / atomic_write_pickle: 写临时文件再 rename,避免半写状态
  - exclusive_lock:      fcntl 文件锁,防止多进程并发写同一资源
  - pid_aware_done:      带 PID + start_time 的标记文件,避免两个重算互相覆盖

为什么需要:
  1. 后台 _spawn_recompute_background 线程 + auto_refresh cron 可能并发跑
  2. 两个进程同时 pickle.dump 到同一文件,中间字节被对方覆盖 -> UnpicklingError
  3. app.py 通过 .recompute_done 文件判定"用户需不需要重读" — 不带 pid 会让第二次
     刷新误以为是第一次完成的标记
"""
from __future__ import annotations

import contextlib
import os
import pickle
import tempfile
import time
from pathlib import Path
from typing import Any


@contextlib.contextmanager
def exclusive_lock(lock_path: Path, timeout: float = 30.0, poll: float = 0.1):
    """fcntl 排他锁(进程级,线程不互斥)

    用法:
        with exclusive_lock(path_to_lockfile):
            ... # 临界区
    timeout: 最长等待秒数,超时抛 TimeoutError
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    start = time.time()
    try:
        try:
            import fcntl  # type: ignore
        except ImportError:
            # 非 POSIX 平台(Windows)直接退化为 noop
            yield
            return
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() - start > timeout:
                    raise TimeoutError(f"acquire lock {lock_path} timeout")
                time.sleep(poll)
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except Exception:
                pass
    finally:
        try:
            os.close(fd)
        except Exception:
            pass


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """原子写二进制: 临时文件 + fsync + rename

    rename 在同一文件系统下是原子操作,读者要么看到旧文件,要么看到完整新文件,
    永远不会看到半写状态。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_pickle(path: Path, obj: Any) -> None:
    atomic_write_bytes(path, pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL))


class PidAwareDone:
    """带 pid + start_time 的"任务完成"标记,防止误判覆盖

    用法:
        done = PidAwareDone(DATA_DIR / ".recompute_done")
        if done.is_alive():
            return  # 已经有别的实例在跑
        done.mark_running()              # 写入本次的 pid/时间
        ...                              # 跑任务
        done.mark_done(n_etfs=207)       # 任务完成后改写为 done
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    def is_alive(self) -> bool:
        """是否还有别的实例正在跑(pid 存活 + 内容为 running)"""
        meta = self._read()
        if not meta or meta.get("status") != "running":
            return False
        pid = meta.get("pid")
        if not pid:
            return False
        try:
            os.kill(pid, 0)  # 不发信号,只检查存活
            return True
        except (ProcessLookupError, PermissionError):
            # 进程已死但标记没清 -> 自动清理
            try:
                self.path.unlink()
            except Exception:
                pass
            return False

    def mark_running(self, extra: dict | None = None) -> None:
        payload = {
            "status": "running",
            "pid": os.getpid(),
            "start_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if extra:
            payload.update(extra)
        atomic_write_text(self.path, repr(payload))

    def mark_done(self, **fields) -> None:
        payload = {
            "status": "done",
            "pid": os.getpid(),
            "done_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        payload.update(fields)
        atomic_write_text(self.path, repr(payload))

    def _read(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            txt = self.path.read_text(encoding="utf-8").strip()
            if not txt:
                return None
            # 用 repr/eval 反序列化(dict 的 repr 是合法 Python 字面量)
            return eval(txt, {"__builtins__": {}}, {})
        except Exception:
            return None