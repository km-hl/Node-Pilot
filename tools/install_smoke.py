#!/usr/bin/env python3
"""Optional disposable Linux install check. Refuses ALL pre-existing NodePilot paths."""
import os
import pty
import select
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nodepilot_pkg.common import root_required, run
from nodepilot_pkg.installation import uninstall
from nodepilot_pkg.manager import Manager


def tty_command(command, replies=("0",), timeout=120):
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(ROOT)
        os.execvp(command[0], command)
    received, sent, completed = bytearray(), 0, False
    start = time.monotonic()
    try:
        while time.monotonic() - start < timeout:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if ready:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    break
                received.extend(chunk)
                prompt_count = bytes(received).count("请输入选项".encode())
                if prompt_count > sent and sent < len(replies):
                    os.write(fd, (replies[sent] + "\n").encode())
                    sent += 1
            result, status = os.waitpid(pid, os.WNOHANG)
            if result:
                completed = True
                if os.waitstatus_to_exitcode(status) != 0:
                    raise AssertionError(received.decode(errors="replace"))
                break
        if not completed:
            if time.monotonic() - start >= timeout:
                raise AssertionError("安装菜单测试超时")
            _, status = os.waitpid(pid, 0)
            completed = True
            assert os.waitstatus_to_exitcode(status) == 0, received.decode(errors="replace")
        assert sent == len(replies), received.decode(errors="replace")
        return received.decode(errors="replace")
    finally:
        os.close(fd)
        if not completed:
            import signal
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, 0)


def main():
    root_required()
    paths = [Path("/opt/nodepilot"), Path("/etc/nodepilot"), Path("/usr/local/bin/nodepilot"),
             Path("/etc/systemd/system/nodepilot-renew.service"), Path("/etc/systemd/system/nodepilot-renew.timer")]
    if any(p.exists() or p.is_symlink() for p in paths):
        raise SystemExit("拒绝在已有 NodePilot 安装上执行破坏性安装测试。")
    manager = Manager()
    try:
        tty_command(["bash", "install.sh", "--local"])
        print("local install automatically opens TTY menu OK")
        sentinel = Path("/etc/nodepilot/.smoke-sentinel")
        sentinel.write_text("preserve")
        os.chmod(sentinel, 0o600)
        tty_command(["bash", "install.sh", "--local"])
        assert sentinel.read_text() == "preserve"
        print("repeat install preserves state OK")
        tty_command(["bash", "install.sh", "--local", "--upgrade"])
        assert sentinel.read_text() == "preserve"
        print("local script upgrade preserves state OK")
        text = tty_command(["nodepilot", "menu"], replies=("invalid", "0"))
        assert "无效选项" in text
        print("invalid menu input loops OK")
        # A shell pipeline consumes stdin; the TUI still reads the controlling PTY.
        tty_command(["bash", "-c", "cat install.sh | bash -s -- --local"])
        print("stdin pipe retains interactive TTY OK")
    finally:
        if Path("/usr/local/bin/nodepilot").is_file():
            with manager.locked():
                uninstall(manager)
            # This root was absent at entry, contains only this test's empty backups.
            state = Path("/etc/nodepilot")
            assert not manager.nodes()
            shutil.rmtree(state)
    assert all(not p.exists() for p in paths)
    print("temporary installation, timer and private test state cleaned")


if __name__ == "__main__":
    main()
