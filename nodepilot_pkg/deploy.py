"""Executed only after the bootstrap verifies the project archive."""
import os
import re
import shutil
import sys
from pathlib import Path

from .common import PilotError, atomic_bytes, atomic_json, fsync_dir, private_dir, run
from .installation import LAUNCHER, RENEW_SERVICE, RENEW_TIMER
from .manager import Manager, OWNER
from .system import supported


def deploy(source, digest, source_url=""):
    supported()
    if not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
        raise PilotError("非法项目包校验值。")
    root, launcher = Path("/opt/nodepilot"), Path("/usr/local/bin/nodepilot")
    if root.exists() and (root.is_symlink() or (root / ".owner").is_symlink() or not (root / ".owner").is_file() or (root / ".owner").read_text() != OWNER):
        raise PilotError("已有程序目录不属于 NodePilot。")
    if launcher.is_symlink() or (launcher.exists() and launcher.read_text() != LAUNCHER):
        raise PilotError("已有同名命令不属于 NodePilot。")
    units = Path("/etc/systemd/system")
    for name, content in (("nodepilot-renew.service", RENEW_SERVICE), ("nodepilot-renew.timer", RENEW_TIMER)):
        p = units / name
        if p.is_symlink() or (p.exists() and p.read_text() != content):
            raise PilotError("同名续期服务不属于 NodePilot。")
        if not p.exists() and run(["systemctl", "show", name, "-p", "FragmentPath", "--value"]):
            raise PilotError("同名续期服务由其他位置提供。")
        if run(["systemctl", "show", name, "-p", "DropInPaths", "--value"]):
            raise PilotError("续期服务存在外部 drop-in，拒绝接管。")
    # Serializes upgrades with all manager and renewal operations.
    manager = Manager()
    with manager.locked():
        private_dir(root)
        atomic_bytes(root / ".owner", OWNER.encode())
        private_dir(root / "cores")
        app = private_dir(root / "app")
        destination = app / digest.lower()
        if not destination.exists():
            stage = app / (".staging-" + os.urandom(4).hex())
            shutil.copytree(source, stage)
            for p in stage.rglob("*"):
                p.chmod(0o700 if p.is_dir() else 0o600)
            atomic_json(stage / "files.json", {"schema": 1, "files": [
                p.relative_to(stage).as_posix() for p in stage.rglob("*")
                if p.is_file() and (p.name == "nodepilot" or p.suffix == ".py")]})
            run(["python3", stage / "nodepilot", "self-check"])
            os.replace(stage, destination)
        current = app / "current"
        old = current.resolve() if current.is_symlink() else None
        if current.exists() and not current.is_symlink():
            raise PilotError("拒绝覆盖非符号链接的程序 current。")
        temporary = app / ".current-new"
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(destination.name, target_is_directory=True)
        os.replace(temporary, current)
        fsync_dir(app)
        previous_launcher = launcher.read_bytes() if launcher.exists() else None
        previous_units = {name: (units / name).read_bytes() if (units / name).exists() else None
                          for name in ("nodepilot-renew.service", "nodepilot-renew.timer")}
        try:
            atomic_bytes(launcher, LAUNCHER.encode(), mode=0o755)
            for name, content in (("nodepilot-renew.service", RENEW_SERVICE), ("nodepilot-renew.timer", RENEW_TIMER)):
                atomic_bytes(units / name, content.encode(), mode=0o644)
            run(["systemctl", "daemon-reload"])
            run(["systemctl", "enable", "--now", "nodepilot-renew.timer"])
            run([launcher, "self-check"])
            from urllib.parse import urlsplit
            u = urlsplit(source_url)
            match = re.fullmatch(r"/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/releases/download/[^/]+/[^/]+", u.path)
            if u.scheme == "https" and u.hostname == "github.com" and match:
                atomic_json(root / "source.json", {"repository": match[1]})
        except (Exception, KeyboardInterrupt):
            if old:
                temporary.symlink_to(old.name, target_is_directory=True)
                os.replace(temporary, current)
            else:
                current.unlink(missing_ok=True)
            if previous_launcher is None:
                launcher.unlink(missing_ok=True)
            else:
                atomic_bytes(launcher, previous_launcher, mode=0o755)
            if previous_units["nodepilot-renew.timer"] is None:
                run(["systemctl", "disable", "--now", "nodepilot-renew.timer"])
            for name, content in previous_units.items():
                if content is None:
                    (units / name).unlink(missing_ok=True)
                else:
                    atomic_bytes(units / name, content, mode=0o644)
            run(["systemctl", "daemon-reload"])
            raise


if __name__ == "__main__":
    try:
        deploy(Path(sys.argv[1]), sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
    except (PilotError, OSError, ValueError) as e:
        print(f"部署失败：{e}", file=sys.stderr)
        raise SystemExit(1)
