"""Owned installation paths only. Backups are intentionally retained."""
import re
from pathlib import Path

from .common import PilotError, read_json, run
from .manager import OWNER

RENEW_SERVICE = """# NodePilot certificate renewal schema=1
[Unit]
Description=NodePilot owned certificate renewal
After=network-online.target
[Service]
Type=oneshot
UMask=0077
ExecStart=/usr/local/bin/nodepilot renew
"""
RENEW_TIMER = """# NodePilot certificate renewal schema=1
[Unit]
Description=NodePilot certificate renewal timer
[Timer]
OnCalendar=*-*-* 04:00:00
RandomizedDelaySec=1h
Persistent=true
[Install]
WantedBy=timers.target
"""
LAUNCHER = "#!/usr/bin/env bash\n# NodePilot owned launcher schema=1\nexec python3 /opt/nodepilot/app/current/nodepilot \"$@\"\n"


def remove_program_files(root):
    """Delete manifest-owned files only; unknown user additions remain on disk."""
    app = root / "app"
    if app.is_dir() and not app.is_symlink():
        for directory in app.iterdir():
            if directory.is_symlink():
                if directory.name == "current" and directory.resolve().parent == app.resolve():
                    directory.unlink()
                continue
            manifest = directory / "files.json"
            if not re.fullmatch(r"[0-9a-f]{64}", directory.name) or not manifest.is_file() or manifest.is_symlink():
                continue
            record = read_json(manifest)
            if record.get("schema") != 1 or any(not re.fullmatch(r"nodepilot|nodepilot_pkg/[a-z_]+\.py", name) for name in record.get("files", [])):
                raise PilotError("程序文件清单非法，保留程序目录。")
            for name in record["files"]:
                target = directory / name
                if target.parent.is_symlink():
                    continue
                target.unlink(missing_ok=True)
            package = directory / "nodepilot_pkg"
            cache = package / "__pycache__"
            if cache.is_dir() and not cache.is_symlink() and not package.is_symlink():
                stems = {Path(x).stem for x in record["files"] if x.endswith(".py")}
                for p in cache.iterdir():
                    if re.fullmatch(r"[a-z_]+\.cpython-\d+(?:\.opt-\d)?\.pyc", p.name) and p.name.split(".")[0] in stems:
                        p.unlink()
                empty_dir(cache)
            if not package.is_symlink():
                empty_dir(package)
            manifest.unlink()
            empty_dir(directory)
        empty_dir(app)
    cores = root / "cores"
    if cores.is_dir() and not cores.is_symlink():
        for name in ("xray", "sing-box", "caddy"):
            parent = cores / name
            if not parent.is_dir() or parent.is_symlink():
                continue
            for directory in parent.iterdir():
                if directory.is_symlink():
                    if directory.name == "current" and directory.resolve().parent == parent.resolve():
                        directory.unlink()
                    continue
                marker = directory / "verified.json"
                if not re.fullmatch(r"\d+\.\d+\.\d+-[0-9a-f]{8}", directory.name) or not marker.is_file() or marker.is_symlink():
                    continue
                (directory / name).unlink(missing_ok=True)
                marker.unlink()
                empty_dir(directory)
            empty_dir(parent)
        empty_dir(cores)
    (root / "source.json").unlink(missing_ok=True)
    # Retain owner marker if unknown additions remain; never recursively remove root.
    if set(p.name for p in root.iterdir()) <= {".owner"}:
        (root / ".owner").unlink()
        root.rmdir()


def empty_dir(path):
    if path.is_dir() and not path.is_symlink() and not any(path.iterdir()):
        path.rmdir()


def uninstall(manager, root=Path("/opt/nodepilot"), launcher=Path("/usr/local/bin/nodepilot"), units=Path("/etc/systemd/system")):
    root, launcher, units = Path(root), Path(launcher), Path(units)
    marker = root / ".owner"
    if root.is_symlink() or marker.is_symlink() or not marker.is_file() or marker.read_text() != OWNER:
        raise PilotError("程序目录缺少所有权标记，拒绝卸载。")
    if launcher.is_symlink() or not launcher.is_file() or launcher.read_text() != LAUNCHER:
        raise PilotError("管理命令不是本项目创建，拒绝删除。")
    for name, content in (("nodepilot-renew.service", RENEW_SERVICE), ("nodepilot-renew.timer", RENEW_TIMER)):
        path = units / name
        if path.is_symlink() or (path.exists() and path.read_text() != content):
            raise PilotError("证书续期单元不是本项目创建。")
        if path.exists() and run(["systemctl", "show", name, "-p", "DropInPaths", "--value"]):
            raise PilotError("续期单元存在外部 drop-in，拒绝自动卸载。")
    for n in manager.nodes():
        manager.services.assert_owned(n)
        from .caddy import own_target
        if own_target(n):
            manager.caddy.assert_owned()
    backup = manager.backup()
    for n in manager.nodes():
        manager.delete(n["id"])
    timer = units / "nodepilot-renew.timer"
    if timer.exists():
        run(["systemctl", "disable", "--now", "nodepilot-renew.timer"])
    for name in ("nodepilot-renew.timer", "nodepilot-renew.service"):
        (units / name).unlink(missing_ok=True)
    run(["systemctl", "daemon-reload"])
    launcher.unlink()
    remove_program_files(root)
    return backup
