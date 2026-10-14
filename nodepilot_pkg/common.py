"""Bounded subprocesses, private durable files and operator errors."""
import json
import os
import subprocess
import tempfile
from pathlib import Path


class PilotError(Exception):
    pass


def run(args, timeout=30, input=None):
    try:
        p = subprocess.run([str(a) for a in args], input=input, text=True,
                           capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PilotError(f"命令失败或超时：{args[0]} ({type(e).__name__})") from e
    if p.returncode:
        # Core error messages may contain configuration credentials: do not echo.
        raise PilotError(f"{args[0]} 返回 {p.returncode}；请在本机检查配置/服务日志（可能含凭据）。")
    return p.stdout.strip()


def run_bytes(args, data, timeout=15):
    """Binary stdin keeps private keys out of command-line arguments."""
    try:
        p = subprocess.run([str(a) for a in args], input=data, capture_output=True,
                           timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PilotError(f"命令失败或超时：{args[0]}") from e
    if p.returncode:
        raise PilotError(f"{args[0]} 无法处理密钥输入；未输出敏感内容。")
    return p.stdout


def private_dir(path):
    path = Path(path)
    if path.is_symlink():
        raise PilotError(f"拒绝目录符号链接：{path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def atomic_bytes(path, data, mode=0o600):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
        fsync_dir(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_json(path, value):
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise PilotError(f"无法读取 JSON：{path}") from e


def fsync_dir(path):
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def root_required():
    if os.name != "posix" or os.geteuid() != 0:
        raise PilotError("管理系统服务需要在支持的 Linux 系统中以 root 运行。")
