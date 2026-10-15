"""Owned installation paths only. Backups are intentionally retained."""
import re
import shutil
from pathlib import Path

from .common import PilotError, atomic_bytes, atomic_json, read_json, run
from .manager import LEGACY_OWNER, OWNER

RENEW_SERVICE = """# NodePilot certificate renewal schema=1
[Unit]
Description=NodePilot owned certificate renewal
After=network-online.target
[Service]
Type=oneshot
UMask=0077
ExecStart=/usr/local/bin/nodepilot renew
"""
LEGACY_RENEW_TIMER = """# NodePilot certificate renewal schema=1
[Unit]
Description=NodePilot certificate renewal timer
[Timer]
OnCalendar=*-*-* 04:00:00
RandomizedDelaySec=1h
Persistent=true
[Install]
WantedBy=timers.target
"""
RENEW_TIMER = LEGACY_RENEW_TIMER.replace('OnCalendar=', 'OnBootSec=2min\nOnCalendar=', 1)
LAUNCHER = "#!/usr/bin/env bash\n# NodePilot owned launcher schema=1\nexec python3 /opt/nodepilot/app/current/nodepilot \"$@\"\n"


def owned_unit(text, expected):
    return text == expected or (expected == RENEW_TIMER and text == LEGACY_RENEW_TIMER)


def preflight_program(root):
    """Validate every manifest before deleting the first program file."""
    app = root / "app"
    if app.is_symlink():
        raise PilotError("程序 app 目录为外部链接，拒绝清理。")
    if app.is_dir():
        for directory in app.iterdir():
            if directory.is_symlink():
                continue
            manifest = directory / "files.json"
            if not re.fullmatch(r"[0-9a-f]{64}", directory.name) or not manifest.exists():
                continue
            if manifest.is_symlink():
                raise PilotError("程序清单为外部链接。")
            record = read_json(manifest)
            if record.get("schema") != 1 or not isinstance(record.get("files"), list) or any(
                    not isinstance(name, str) or not re.fullmatch(r"nodepilot|nodepilot_pkg/[a-z_]+\.py", name) for name in record["files"]):
                raise PilotError("程序文件清单非法；未删除任何程序文件。")
            if (directory / "nodepilot_pkg").is_symlink():
                raise PilotError("程序包目录为外部链接。")


def remove_program_files(root):
    """Delete manifest-owned files only; unknown user additions remain on disk."""
    preflight_program(root)
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
    if root.is_symlink() or marker.is_symlink() or not marker.is_file() or marker.read_text() not in {OWNER, LEGACY_OWNER}:
        raise PilotError("程序目录缺少所有权标记，拒绝卸载。")
    if launcher.is_symlink() or not launcher.is_file() or launcher.read_text() != LAUNCHER:
        raise PilotError("管理命令不是本项目创建，拒绝删除。")
    for name, content in (("nodepilot-renew.service", RENEW_SERVICE), ("nodepilot-renew.timer", RENEW_TIMER)):
        path = units / name
        if path.is_symlink() or (path.exists() and not owned_unit(path.read_text(),content)):
            raise PilotError("证书续期单元不是本项目创建。")
        if path.exists() and run(["systemctl", "show", name, "-p", "DropInPaths", "--value"]):
            raise PilotError("续期单元存在外部 drop-in，拒绝自动卸载。")
    from .adoption import Adoptions
    Adoptions(manager).assert_can_uninstall()
    preflight_program(root)
    for n in manager.nodes():
        manager.services.assert_owned(n)
        from .caddy import own_target
        if own_target(n):
            manager.caddy.assert_owned()
    if (manager.services.units/'nodepilot-caddy.service').exists():
        manager.caddy.assert_owned()
    pending = manager.state / "uninstall.json"
    if pending.exists() and read_json(pending).get("phase") not in {"rolled_back", "complete"}:
        raise PilotError("已有卸载事务；请先重新进入管理器恢复。")
    backup = manager.backup()
    record = {"schema": 1, "phase": "removing_services", "root": str(root), "launcher": str(launcher),
              "units": str(units), "backup": str(backup), "nodes": manager.nodes(), "removed": [],
              "running": {n["id"]: manager.services.active(n) for n in manager.nodes()},
              "enabled": {n["id"]: run(["systemctl", "show", n["service"], "-p", "UnitFileState", "--value"]) for n in manager.nodes()},
              "renew": {name: (units / name).exists() for name in ("nodepilot-renew.service", "nodepilot-renew.timer")},
              "timer_enabled": run(["systemctl", "show", "nodepilot-renew.timer", "-p", "UnitFileState", "--value"]),
              "timer_active": run(["systemctl", "show", "nodepilot-renew.timer", "-p", "ActiveState", "--value"]) == "active",
              "caddy_active": manager.caddy.active()}
    atomic_json(pending, record)
    try:
        for n in record["nodes"]:
            manager.services.remove(n)
            record["removed"].append(n["id"])
            atomic_json(pending, record)
        manager.caddy.sync([])
        if record["renew"]["nodepilot-renew.timer"]:
            run(["systemctl", "disable", "--now", "nodepilot-renew.timer"])
        for name in record["renew"]:
            (units / name).unlink(missing_ok=True)
        run(["systemctl", "daemon-reload"])
        record["phase"] = "finalizing"
        atomic_json(pending, record)
    except (Exception, KeyboardInterrupt) as error:
        try:
            recover_uninstall(manager, root, launcher, units)
        except Exception as rollback:
            raise PilotError(f"卸载失败，恢复未完成：{rollback}；保留 uninstall.json 和私有备份，重新进入菜单恢复。") from error
        raise PilotError("卸载失败；节点配置未删除，已恢复原服务状态。") from error
    finish_uninstall(manager, record)
    return backup


def finish_uninstall(manager, record):
    """After this boundary rollback is unavailable; idempotently finish owned cleanup."""
    from .model import validate
    for node in record['nodes']:
        validate(node)
        manager.services.assert_owned(node,missing=True)
    for node in record['nodes']:
        if (manager.services.units/node['service']).exists():
            manager.services.remove(node)
    for node in record["nodes"]:
        validate(node)
        path = manager.node_dir(node["id"])
        if path.exists():
            shutil.rmtree(path)
        acme = manager.state / "acme" / node["id"]
        if acme.is_dir() and not acme.is_symlink():
            shutil.rmtree(acme)
    units, launcher, root = map(Path, (record["units"], record["launcher"], record["root"]))
    # A repaired installation may have recreated the timer: check before touching it.
    for name, content in (("nodepilot-renew.service", RENEW_SERVICE), ("nodepilot-renew.timer", RENEW_TIMER)):
        path = units / name
        if path.is_symlink() or (path.exists() and not owned_unit(path.read_text(),content)):
            raise PilotError("最终清理遇到外部续期单元；保留卸载记录。")
        if path.exists():
            if run(["systemctl", "show", name, "-p", "DropInPaths", "--value"]):
                raise PilotError("最终清理遇到外部 drop-in。")
            if name.endswith("timer"):
                run(["systemctl", "disable", "--now", name])
            path.unlink()
    run(["systemctl", "daemon-reload"])
    if launcher.is_symlink() or (launcher.exists() and launcher.read_text() != LAUNCHER):
        raise PilotError("管理命令已被外部修改；保留卸载记录。")
    if root.exists():
        if root.is_symlink() or (root / ".owner").is_symlink() or (root / ".owner").read_text() not in {OWNER, LEGACY_OWNER}:
            raise PilotError("程序目录所有权变化；保留卸载记录。")
        remove_program_files(root)
    launcher.unlink(missing_ok=True)
    record["phase"] = "complete"
    atomic_json(manager.state / "uninstall.json", record)


def recover_uninstall(manager, root=Path("/opt/nodepilot"), launcher=Path("/usr/local/bin/nodepilot"), units=Path("/etc/systemd/system")):
    path = manager.state / "uninstall.json"
    if not path.exists():
        return False
    record = read_json(path)
    if record.get("schema") != 1 or any(record.get(k) != str(v) for k, v in (("root", root), ("launcher", launcher), ("units", units))):
        raise PilotError("卸载事务路径不匹配；拒绝跨目录恢复。")
    if record["phase"] in {"complete", "rolled_back"}:
        return False
    if record["phase"] == "finalizing":
        finish_uninstall(manager, record)
        return True
    if record["phase"] not in {"removing_services", "rolling_back"}:
        raise PilotError("未知卸载事务状态。")
    record["phase"] = "rolling_back"
    atomic_json(path, record)
    for n in record["nodes"]:
        if manager.get(n["id"]) != n:
            raise PilotError("卸载期间节点修订已变化；拒绝覆盖。")
        manager.services.assert_owned(n, missing=True)
        if not (manager.services.units / n["service"]).exists():
            manager.services.install(n)
        if record["enabled"].get(n["id"]) in {'disabled','enabled-runtime'}:
            run(["systemctl", "disable", n["service"]])
            if record['enabled'][n['id']]=='enabled-runtime':
                run(['systemctl','enable','--runtime',n['service']])
        if record["running"][n["id"]]:
            manager.services.restart(n)
    manager.caddy.sync(manager.nodes())
    if not record["caddy_active"] and any(n["reality"].get("mode") == "self" for n in record["nodes"] if n["protocol"] == "reality"):
        run(["systemctl", "stop", "nodepilot-caddy.service"])
    for name, content in (("nodepilot-renew.service", RENEW_SERVICE), ("nodepilot-renew.timer", RENEW_TIMER)):
        unit = Path(units) / name
        if unit.is_symlink() or (unit.exists() and not owned_unit(unit.read_text(),content)) or run(["systemctl", "show", name, "-p", "DropInPaths", "--value"]):
            raise PilotError("恢复遇到外部续期单元；保留记录。")
        if record["renew"][name]:
            atomic_bytes(unit, content.encode(), mode=0o644)
    run(["systemctl", "daemon-reload"])
    if record['renew']['nodepilot-renew.timer'] and record["timer_enabled"] in {"enabled", "enabled-runtime"}:
        run(["systemctl", "enable", *(["--runtime"] if record["timer_enabled"] == "enabled-runtime" else []), "nodepilot-renew.timer"])
    if record['renew']['nodepilot-renew.timer'] and record["timer_active"]:
        run(["systemctl", "start", "nodepilot-renew.timer"])
    record["phase"] = "rolled_back"
    atomic_json(path, record)
    return False
