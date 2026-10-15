"""Executed only after the bootstrap verifies the project archive."""
import os
import hashlib
import re
import shutil
import sys
from pathlib import Path

from .common import PilotError, atomic_bytes, atomic_json, fsync_dir, private_dir, read_json, run
from .installation import LAUNCHER, RENEW_SERVICE, RENEW_TIMER, owned_unit
from .manager import LEGACY_OWNER, Manager, OWNER
from .system import supported


def assert_program_owned(root, launcher, units):
    if root.exists() and (root.is_symlink() or (root / '.owner').is_symlink() or
            not (root / '.owner').is_file() or (root / '.owner').read_text() not in {OWNER,LEGACY_OWNER}):
        raise PilotError('已有程序目录不属于兼容的 NodePilot schema。请使用能够理解现有事务的更新管理器。')
    if (root/'app').is_symlink():
        raise PilotError('拒绝外部程序目录链接。')
    if launcher.is_symlink() or (launcher.exists() and launcher.read_text()!=LAUNCHER):
        raise PilotError('已有同名命令不属于 NodePilot。')
    for name, content in (("nodepilot-renew.service",RENEW_SERVICE),("nodepilot-renew.timer",RENEW_TIMER)):
        path=units/name
        if path.is_symlink() or (path.exists() and not owned_unit(path.read_text(),content)):
            raise PilotError('同名续期单元不属于 NodePilot。')
        if not path.exists() and run(['systemctl','show',name,'-p','FragmentPath','--value']):
            raise PilotError('同名续期单元由其他位置提供。')
        if run(['systemctl','show',name,'-p','DropInPaths','--value']):
            raise PilotError('续期单元存在外部 drop-in。')


def recover_program(root=Path('/opt/nodepilot'), launcher=Path('/usr/local/bin/nodepilot'), units=Path('/etc/systemd/system'), manager=None):
    """Finish a durable, prevalidated upgrade intent; never run an older installer.

    Caller holds the state lock. Before the intent is durable current is unchanged;
    afterwards recovery finishes installation. Ordinary failures roll back in deploy.
    """
    root, launcher, units = map(Path,(root,launcher,units))
    journal=root/'program-pending.json'
    if not journal.exists():
        return False
    assert_program_owned(root,launcher,units)
    if journal.is_symlink():
        raise PilotError('程序事务为外部链接。')
    record=read_json(journal)
    if record.get('schema')!=2 or record.get('phase')!='installing' or any(
            record.get(k)!=str(v) for k,v in (('root',root),('launcher',launcher),('units',units))):
        raise PilotError('程序升级事务 schema/路径不匹配；请用适配 schema 的新安装器恢复。')
    digest=record.get('destination','')
    if not re.fullmatch('[0-9a-f]{64}',digest):
        raise PilotError('程序升级目标无效。')
    app=root/'app';destination=app/digest
    if destination.is_symlink() or not destination.is_dir():
        raise PilotError('程序候选目录缺失/越界，保留事务；请用当前候选安装器修复。')
    manifest=read_json(destination/'files.json')
    if manifest.get('recovery_schema')!=2:
        raise PilotError('候选管理器不支持此恢复 schema。')
    from .installation import preflight_program
    preflight_program(root)
    hashes=manifest.get('hashes',{})
    if set(hashes)!=set(manifest['files']) or 'nodepilot' not in hashes:
        raise PilotError('程序恢复缺少完整文件校验清单。')
    for name,expected_hash in hashes.items():
        path=destination/name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected_hash:
            raise PilotError('程序候选文件校验失败，保留事务并拒绝执行。')
    repository=record.get('repository')
    if repository and (not isinstance(repository,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository)):
        raise PilotError('程序升级源无效。')
    run(['python3',destination/'nodepilot','self-check'])
    current=app/'current'
    if current.exists() and not current.is_symlink():
        raise PilotError('程序 current 不是所属链接。')
    if current.is_symlink() and (current.resolve().parent!=app.resolve() or current.resolve().name not in {digest,record.get('old')}):
        raise PilotError('程序 current 已被其他事务修改。')
    temporary=app/'.current-new'
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(destination.name,target_is_directory=True)
    os.replace(temporary,current);fsync_dir(app)
    # schema=2 deliberately makes v0.3/RC1 refuse the installation/state roots.
    atomic_bytes(root/'.owner',OWNER.encode())
    if manager is not None:
        atomic_bytes(manager.state/'.owner',OWNER.encode())
    atomic_bytes(launcher,LAUNCHER.encode(),mode=0o755)
    for name,content in (('nodepilot-renew.service',RENEW_SERVICE),('nodepilot-renew.timer',RENEW_TIMER)):
        atomic_bytes(units/name,content.encode(),mode=0o644)
    run(['systemctl','daemon-reload'])
    run(['systemctl','enable','--now','nodepilot-renew.timer'])
    run(['python3',destination/'nodepilot','self-check'])
    if repository:
        atomic_json(root/'source.json',{'repository':repository})
    journal.unlink();fsync_dir(root)
    return True


def deploy(source, digest, source_url="", root=Path('/opt/nodepilot'), launcher=Path('/usr/local/bin/nodepilot'),
           units=Path('/etc/systemd/system'), manager=None):
    supported()
    if not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
        raise PilotError("非法项目包校验值。")
    root, launcher, units = map(Path,(root,launcher,units))
    assert_program_owned(root,launcher,units)
    # Serializes upgrades with all manager and renewal operations.
    manager = manager or Manager(migrate_owner=False)
    state_marker=manager.state/'.owner'
    with manager.locked():
        recover_program(root,launcher,units,manager)
        from .installation import recover_uninstall
        recover_uninstall(manager,root,launcher,units)
        manager.recover_core()
        manager.recover()
        from .adoption import Adoptions
        Adoptions(manager).recover()
        previous_owner=(root/'.owner').read_bytes() if (root/'.owner').is_file() else None
        previous_state_owner=state_marker.read_bytes() if state_marker.is_file() else None
        backup=manager.backup()
        private_dir(root)
        if not (root/'.owner').exists():
            atomic_bytes(root / ".owner", OWNER.encode())
        private_dir(root / "cores")
        app = private_dir(root / "app")
        destination = app / digest.lower()
        if not destination.exists():
            stage = app / (".staging-" + os.urandom(4).hex())
            shutil.copytree(source, stage)
            for p in stage.rglob("*"):
                p.chmod(0o700 if p.is_dir() else 0o600)
            files=[
                p.relative_to(stage).as_posix() for p in stage.rglob("*")
                if p.is_file() and (p.name == "nodepilot" or p.suffix == ".py")]
            atomic_json(stage / "files.json", {"schema": 1, "recovery_schema":2, "files":files,
                "hashes":{name:hashlib.sha256((stage/name).read_bytes()).hexdigest() for name in files}})
            run(["python3", stage / "nodepilot", "self-check"])
            os.replace(stage, destination)
        current = app / "current"
        old = current.resolve() if current.is_symlink() else None
        if old and (old.parent!=app.resolve() or not re.fullmatch('[0-9a-f]{64}',old.name)):
            raise PilotError('程序旧修订越界。')
        if current.exists() and not current.is_symlink():
            raise PilotError("拒绝覆盖非符号链接的程序 current。")
        temporary = app / ".current-new"
        temporary.unlink(missing_ok=True)
        previous_launcher = launcher.read_bytes() if launcher.exists() else None
        previous_units = {name: (units / name).read_bytes() if (units / name).exists() else None
                          for name in ("nodepilot-renew.service", "nodepilot-renew.timer")}
        timer_enabled=run(['systemctl','show','nodepilot-renew.timer','-p','UnitFileState','--value'])
        timer_active=run(['systemctl','show','nodepilot-renew.timer','-p','ActiveState','--value'])=='active'
        try:
            from urllib.parse import urlsplit
            u = urlsplit(source_url)
            match = re.fullmatch(r"/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/releases/download/[^/]+/[^/]+", u.path)
            repository=match[1] if u.scheme=='https' and u.hostname=='github.com' and match else ''
            atomic_bytes(root/'.owner',OWNER.encode())
            atomic_json(root/'program-pending.json',{'schema':2,'phase':'installing','root':str(root),
                'launcher':str(launcher),'units':str(units),'destination':destination.name,
                'old':old.name if old else None,'backup':str(backup),'repository':repository})
            recover_program(root,launcher,units,manager)
        except (Exception, KeyboardInterrupt):
            if old:
                temporary.unlink(missing_ok=True)
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
            if previous_units['nodepilot-renew.timer'] is not None:
                if timer_enabled not in {'enabled','enabled-runtime'}:
                    run(['systemctl','disable','nodepilot-renew.timer'])
                elif timer_enabled=='enabled-runtime':
                    run(['systemctl','disable','nodepilot-renew.timer'])
                    run(['systemctl','enable','--runtime','nodepilot-renew.timer'])
                if not timer_active:
                    run(['systemctl','stop','nodepilot-renew.timer'])
            if previous_owner is not None:
                atomic_bytes(root/'.owner',previous_owner)
            if previous_state_owner is not None:
                atomic_bytes(state_marker,previous_state_owner)
            (root/'program-pending.json').unlink(missing_ok=True)
            raise


if __name__ == "__main__":
    try:
        deploy(Path(sys.argv[1]), sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
    except (PilotError, OSError, ValueError) as e:
        print(f"部署失败：{e}", file=sys.stderr)
        raise SystemExit(1)
