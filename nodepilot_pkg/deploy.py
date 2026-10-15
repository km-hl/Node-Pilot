"""Verified program upgrades with durable forward recovery and retryable rollback."""
import hashlib
import os
import re
import shutil
import sys
from pathlib import Path

from .common import PilotError, atomic_bytes, atomic_json, fsync_dir, private_dir, read_json, run
from .installation import (LAUNCHER, RENEW_SERVICE, RENEW_TIMER, TIMER, owned_unit,
                           preflight_program, restore_timer, timer_state)
from .manager import LEGACY_OWNER, Manager, OWNER
from .system import supported

UNITS = {'nodepilot-renew.service': RENEW_SERVICE, TIMER: RENEW_TIMER}
PHASES = {'preparing', 'installing', 'rolling_back', 'complete'}
REQUIRED = {'nodepilot', *('nodepilot_pkg/' + name + '.py'
                         for name in ('__init__', 'cli', 'deploy', 'manager', 'installation'))}


def assert_program_owned(root, launcher, units):
    if root.exists() and (root.is_symlink() or (root / '.owner').is_symlink() or
            not (root / '.owner').is_file() or (root / '.owner').read_text() not in {OWNER, LEGACY_OWNER}):
        raise PilotError('已有程序目录不属于兼容的 NodePilot schema。请使用能够理解现有事务的更新管理器。')
    if (root / 'app').is_symlink():
        raise PilotError('拒绝外部程序目录链接。')
    if launcher.is_symlink() or (launcher.exists() and launcher.read_text() != LAUNCHER):
        raise PilotError('已有同名命令不属于 NodePilot。')
    for name, content in UNITS.items():
        path = units / name
        if path.is_symlink() or (path.exists() and not owned_unit(path.read_text(), content)):
            raise PilotError('同名续期单元不属于 NodePilot。')
        fragment = run(['systemctl', 'show', name, '-p', 'FragmentPath', '--value'])
        if fragment and fragment != str(path):
            raise PilotError('同名续期单元由其他位置提供。')
        if run(['systemctl', 'show', name, '-p', 'DropInPaths', '--value']):
            raise PilotError('续期单元存在外部 drop-in。')


def text_or_none(path):
    if path.is_symlink():
        raise PilotError('事务文件为外部链接，拒绝覆盖。')
    return path.read_bytes().decode('utf-8') if path.exists() else None


def version(directory):
    path = directory / 'nodepilot_pkg/__init__.py'
    if not path.is_file() or path.is_symlink():
        return None
    match = re.search(r'__version__\s*=\s*[\'"]([0-9.]+)[\'"]', path.read_text())
    return match[1] if match else None


def snapshot(root, launcher, units, manager, old, legacy=False):
    previous = {name: text_or_none(units / name) for name in UNITS}
    exists = previous[TIMER] is not None
    if exists:
        state = timer_state(run)
    else:
        # An absent timer is a first install only when there was no old program.
        # Legacy RC2 logs lack history: don't invent consent to enable a timer.
        enabled = run(['systemctl', 'show', TIMER, '-p', 'UnitFileState', '--value'])
        active = run(['systemctl', 'show', TIMER, '-p', 'ActiveState', '--value'])
        if enabled not in {'', 'disabled'} or active not in {'', 'inactive'}:
            raise PilotError('缺失 timer 的 systemd 状态异常；拒绝覆盖。')
        state = {'unit_file_state': 'disabled', 'active_state': 'inactive'}
    return {'units': previous, 'timer_exists': exists, 'timer': state,
            'first_install': old is None and not exists,
            'launcher': text_or_none(launcher), 'root_owner': text_or_none(root / '.owner'),
            'state_owner': text_or_none(manager.state / '.owner') if manager else None,
            'state': str(manager.state) if manager else None,
            'source': text_or_none(root / 'source.json'),
            'provenance': 'legacy-observed' if legacy else 'before-upgrade'}


def validate_record(record, root, launcher, units, manager):
    if not isinstance(record, dict) or not {'schema', 'phase', 'root', 'launcher', 'units', 'old', 'destination'} <= set(record) or record.get('schema') != 2 or record.get('phase') not in PHASES or any(
            record.get(k) != str(v) for k, v in (('root', root), ('launcher', launcher), ('units', units))):
        raise PilotError('程序升级事务 schema/阶段/路径不匹配；保留恢复证据。')
    for key in ('destination', 'old'):
        value = record.get(key)
        if (key == 'destination' or value is not None) and (not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)):
            raise PilotError('程序升级修订无效；保留事务。')
    repository = record.get('repository')
    if repository and (not isinstance(repository, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)):
        raise PilotError('程序升级源无效。')
    if 'transaction_version' not in record:
        if 'previous' in record or record['phase'] != 'installing':
            raise PilotError('程序事务缺少版本；保留证据。')
        return  # Original RC2 schema=2 intent, migrated durably before any changes.
    if record['transaction_version'] != 1:
        raise PilotError('未知程序事务版本；保留证据。')
    p = record.get('previous')
    if not isinstance(p, dict) or not isinstance(p.get('units'), dict) or set(p['units']) != set(UNITS):
        raise PilotError('程序事务缺少原单元快照。')
    for name, content in p['units'].items():
        if content is not None and (not isinstance(content, str) or not owned_unit(content, UNITS[name])):
            raise PilotError('事务中的原单元不属于 NodePilot。')
    if (type(p.get('timer_exists')) is not bool or p['timer_exists'] != (p['units'][TIMER] is not None)
            or type(p.get('first_install')) is not bool or p['first_install'] != (record['old'] is None and not p['timer_exists'])
            or p.get('provenance') not in {'before-upgrade', 'legacy-observed'}
            or not isinstance(p.get('timer'), dict) or set(p['timer']) != {'unit_file_state', 'active_state'}
            or p['timer']['unit_file_state'] not in {'enabled', 'enabled-runtime', 'disabled'}
            or p['timer']['active_state'] not in {'active', 'inactive'}):
        raise PilotError('事务中的 timer 状态无效。')
    for key in ('root_owner', 'state_owner'):
        if key not in p or p[key] not in (None, OWNER, LEGACY_OWNER):
            raise PilotError('事务中的原所有权标记无效。')
    if 'launcher' not in p or p['launcher'] not in (None, LAUNCHER) or 'source' not in p or (p['source'] is not None and not isinstance(p['source'], str)):
        raise PilotError('事务中的原程序文件无效。')
    if p.get('state') is not None and (manager is None or p['state'] != str(manager.state)):
        raise PilotError('程序事务需要匹配的配置目录及状态锁。')
    hashes = record.get('hashes')
    if not isinstance(hashes, dict) or 'nodepilot' not in hashes or any(
            not isinstance(n, str) or not re.fullmatch(r'nodepilot|nodepilot_pkg/[a-z_]+\.py', n)
            or not isinstance(h, str) or not re.fullmatch('[0-9a-f]{64}', h) for n, h in hashes.items()):
        raise PilotError('事务缺少完整目标 SHA-256。')


def verify_candidate(destination, expected=None, expected_version=None):
    if destination.is_symlink() or not destination.is_dir():
        raise PilotError('程序候选目录缺失/越界，保留事务。')
    preflight_program(destination.parent.parent)
    manifest_path = destination / 'files.json'
    if manifest_path.is_symlink() or (destination / 'nodepilot_pkg').is_symlink():
        raise PilotError('候选清单或包目录为外部链接。')
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or not isinstance(manifest.get('files'), list) or any(not isinstance(n, str) for n in manifest['files']):
        raise PilotError('候选清单格式无效；保留事务。')
    hashes = manifest.get('hashes')
    if isinstance(hashes, dict) and not REQUIRED <= set(hashes):
        raise PilotError('程序候选文件校验失败：缺少必要管理器文件；保留事务。')
    if (manifest.get('schema') != 1 or manifest.get('recovery_schema') != 2
            or not isinstance(hashes, dict) or set(hashes) != set(manifest.get('files', []))
            or 'nodepilot' not in hashes or (expected is not None and hashes != expected)
            or any(not re.fullmatch(r'nodepilot|nodepilot_pkg/[a-z_]+\.py', n)
                   or not isinstance(h, str) or not re.fullmatch('[0-9a-f]{64}', h) for n, h in hashes.items())):
        raise PilotError('程序恢复缺少匹配的完整文件校验清单。')
    actual = {p.relative_to(destination).as_posix() for p in destination.rglob('*.py')}
    if actual != set(hashes) - {'nodepilot'}:
        raise PilotError('候选程序包含未校验模块。')
    for name, expected_hash in hashes.items():
        path = destination / name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise PilotError('程序候选文件校验失败，保留事务并拒绝执行。')
    if expected_version is not None and version(destination) != expected_version:
        raise PilotError('候选程序版本与事务记录不匹配；保留事务。')
    return hashes


def assert_old_revision(app, name, expected_version=None):
    if name is not None:
        old = app / name
        if old.is_symlink() or not old.is_dir() or not (old / 'nodepilot').is_file() or (old / 'nodepilot').is_symlink() or (old / 'nodepilot_pkg').is_symlink():
            raise PilotError('原程序修订缺失或为外部链接；保留事务。')
        if expected_version is not None and version(old) != expected_version:
            raise PilotError('原程序版本与事务记录不匹配；保留事务。')


def point(app, name):
    temporary = app / '.current-new'
    if temporary.exists() and not temporary.is_symlink():
        raise PilotError('临时 current 路径不属于 NodePilot。')
    temporary.unlink(missing_ok=True)
    if name is None:
        (app / 'current').unlink(missing_ok=True)
    else:
        temporary.symlink_to(name, target_is_directory=True)
        os.replace(temporary, app / 'current')
    fsync_dir(app)


def restore_file(path, content, mode=0o600):
    if content is None:
        path.unlink(missing_ok=True)
        fsync_dir(path.parent)
    else:
        atomic_bytes(path, content.encode(), mode=mode)


def checkpoint(journal, record, step):
    record['step'] = step
    atomic_json(journal, record)


def rollback_program(record, root, launcher, units, manager):
    """Journal remains rolling_back until every restore, including systemd, succeeds."""
    journal = root / 'program-pending.json'
    record['phase'] = 'rolling_back'
    checkpoint(journal, record, 'rollback')
    p = record['previous']
    if record['old'] is not None and (root / 'app' / record['old']).is_symlink():
        raise PilotError('原程序修订为外部链接；保留事务。')
    if record['old'] is not None and not (root / 'app' / record['old']).is_dir():
        raise PilotError('原程序修订缺失；保留事务。')
    point(root / 'app', record['old'])
    restore_file(launcher, p['launcher'], 0o755)
    # Keep the unit available until its symlinks and running state are removed.
    if not p['timer_exists'] and (units / TIMER).exists():
        run(['systemctl', 'stop', TIMER])
        run(['systemctl', 'disable', TIMER])
        run(['systemctl', 'disable', '--runtime', TIMER])
    for name, content in p['units'].items():
        restore_file(units / name, content, 0o644)
    run(['systemctl', 'daemon-reload'])
    if p['timer_exists']:
        restore_timer(p['timer'], run)
    restore_file(root / 'source.json', p['source'])
    if manager is not None:
        restore_file(manager.state / '.owner', p['state_owner'])
    # First-install failure retains an owned empty scaffold for safe retry.
    if p['root_owner'] is not None:
        restore_file(root / '.owner', p['root_owner'])
    journal.unlink()
    fsync_dir(root)


def recover_program(root=Path('/opt/nodepilot'), launcher=Path('/usr/local/bin/nodepilot'), units=Path('/etc/systemd/system'), manager=None):
    """Caller holds manager.locked(); new intents resume or finish durable rollback."""
    root, launcher, units = map(Path, (root, launcher, units))
    journal = root / 'program-pending.json'
    if journal.is_symlink():
        raise PilotError('程序事务为外部链接。')
    if not journal.exists():
        return False
    if not journal.is_file() or journal.stat().st_size > 256 * 1024:
        raise PilotError('程序事务不是有界普通文件；保留证据并拒绝读取。')
    assert_program_owned(root, launcher, units)
    record = read_json(journal)
    validate_record(record, root, launcher, units, manager)
    app = root / 'app'
    assert_old_revision(app, record['old'], record.get('old_version'))
    current = app / 'current'
    if (current.exists() and not current.is_symlink()) or (current.is_symlink() and (
            current.resolve().parent != app.resolve() or current.resolve().name not in {record['destination'], record.get('old')})):
        raise PilotError('程序 current 已被外部修改；保留事务。')
    destination = app / record['destination']
    if 'transaction_version' not in record:
        # RC2 did not persist history. Keep observed policy and original evidence.
        verify_candidate(destination)
        evidence = root / 'program-pending-rc2.json'
        if evidence.is_symlink() or (evidence.exists() and read_json(evidence) != record):
            raise PilotError('旧事务恢复证据冲突；保留原始记录。')
        atomic_json(evidence, record)
        record['previous'] = snapshot(root, launcher, units, manager, record.get('old'), legacy=True)
        record['hashes'] = verify_candidate(destination)
        record['transaction_version'] = 1
        checkpoint(journal, record, 'legacy-migrated')
    if record['phase'] == 'rolling_back':
        rollback_program(record, root, launcher, units, manager)
        return True
    if record['phase'] == 'preparing':
        # Interrupted copies are never executed; original installation is intact.
        rollback_program(record, root, launcher, units, manager)
        return True
    # Reject corruption without rollback/deletion, preserving forensic evidence.
    verify_candidate(destination, record['hashes'], record.get('target_version'))
    try:
        run(['python3', destination / 'nodepilot', 'self-check'])
        point(app, destination.name)
        checkpoint(journal, record, 'current')
        atomic_bytes(root / '.owner', OWNER.encode())
        if manager is not None:
            atomic_bytes(manager.state / '.owner', OWNER.encode())
        checkpoint(journal, record, 'owners')
        atomic_bytes(launcher, LAUNCHER.encode(), mode=0o755)
        checkpoint(journal, record, 'launcher')
        for name, content in UNITS.items():
            atomic_bytes(units / name, content.encode(), mode=0o644)
        checkpoint(journal, record, 'units')
        run(['systemctl', 'daemon-reload'])
        checkpoint(journal, record, 'reloaded')
        p = record['previous']
        desired = {'unit_file_state': 'enabled', 'active_state': 'active'} if p['first_install'] else p['timer']
        restore_timer(desired, run)
        checkpoint(journal, record, 'timer')
        run(['python3', destination / 'nodepilot', 'self-check'])
        if record.get('repository'):
            atomic_json(root / 'source.json', {'repository': record['repository']})
        record['phase'] = 'complete'
        checkpoint(journal, record, 'checked')
    except (Exception, KeyboardInterrupt) as error:
        try:
            rollback_program(record, root, launcher, units, manager)
        except (Exception, KeyboardInterrupt) as rollback:
            raise PilotError(f'程序升级失败，回滚未完成：{rollback}；保留 program-pending.json，请重试恢复。') from error
        raise
    journal.unlink()
    fsync_dir(root)
    return True


def deploy(source, digest, source_url='', root=Path('/opt/nodepilot'), launcher=Path('/usr/local/bin/nodepilot'),
           units=Path('/etc/systemd/system'), manager=None):
    supported()
    if not re.fullmatch(r'[a-fA-F0-9]{64}', digest):
        raise PilotError('非法项目包校验值。')
    root, launcher, units = map(Path, (root, launcher, units))
    manager = manager or Manager(migrate_owner=False)
    with manager.locked(migrate_owner=False):
        assert_program_owned(root, launcher, units)
        recover_program(root, launcher, units, manager)
        from .installation import recover_uninstall
        recover_uninstall(manager, root, launcher, units)
        manager.recover_core()
        manager.recover()
        from .adoption import Adoptions
        Adoptions(manager).recover()
        current = root / 'app/current'
        old = current.resolve() if current.is_symlink() else None
        if old and (old.parent != (root / 'app').resolve() or not re.fullmatch('[0-9a-f]{64}', old.name) or not old.is_dir()):
            raise PilotError('程序旧修订越界/缺失。')
        if current.exists() and not current.is_symlink():
            raise PilotError('拒绝覆盖非符号链接的程序 current。')
        assert_old_revision(root / 'app', old.name if old else None)
        source = Path(source)
        files = sorted(p.relative_to(source).as_posix() for p in source.rglob('*')
                       if p.is_file() and (p.name == 'nodepilot' or p.suffix == '.py'))
        if any(p.is_symlink() for p in source.rglob('*')) or source.is_symlink():
            raise PilotError('候选源码含外部链接。')
        hashes = {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in files}
        # Recovery may already have installed this exact verified payload. Do
        # not create another transaction/backup or reload/restart units on retry.
        if old and old.name == digest.lower():
            verify_candidate(old, hashes)
            if (text_or_none(launcher) == LAUNCHER and text_or_none(root / '.owner') == OWNER
                    and text_or_none(manager.state / '.owner') == OWNER
                    and all(text_or_none(units / name) == content for name, content in UNITS.items())):
                return
        previous = snapshot(root, launcher, units, manager, old.name if old else None)
        backup = manager.backup()
        private_dir(root)
        if not (root / '.owner').exists():
            atomic_bytes(root / '.owner', OWNER.encode())
        private_dir(root / 'cores')
        app = private_dir(root / 'app')
        destination = app / digest.lower()
        from urllib.parse import urlsplit
        url = urlsplit(source_url)
        match = re.fullmatch(r'/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/releases/download/[^/]+/[^/]+', url.path)
        record = {'schema': 2, 'transaction_version': 1, 'phase': 'preparing', 'step': 'intent',
                  'root': str(root), 'launcher': str(launcher), 'units': str(units),
                  'destination': destination.name, 'old': old.name if old else None,
                  'old_version': version(old) if old else None, 'target_version': version(source),
                  'backup': str(backup), 'previous': previous,
                  'repository': match[1] if url.scheme == 'https' and url.hostname == 'github.com' and match else '',
                  'hashes': hashes}
        validate_record(record, root, launcher, units, manager)
        journal = root / 'program-pending.json'
        atomic_json(journal, record)
        try:
            if not destination.exists():
                stage = app / ('.staging-' + os.urandom(4).hex())
                shutil.copytree(source, stage, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
                for path in stage.rglob('*'):
                    path.chmod(0o700 if path.is_dir() else 0o600)
                atomic_json(stage / 'files.json', {'schema': 1, 'recovery_schema': 2,
                            'files': files, 'hashes': record['hashes']})
                verify_candidate(stage, record['hashes'])
                run(['python3', stage / 'nodepilot', 'self-check'])
                os.replace(stage, destination)
                fsync_dir(app)
            verify_candidate(destination, record['hashes'])
            record['phase'] = 'installing'
            checkpoint(journal, record, 'candidate')
        except (Exception, KeyboardInterrupt) as error:
            try:
                rollback_program(record, root, launcher, units, manager)
            except (Exception, KeyboardInterrupt) as rollback:
                raise PilotError(f'程序准备失败，回滚未完成：{rollback}；保留 program-pending.json。') from error
            raise
        recover_program(root, launcher, units, manager)


if __name__ == '__main__':
    try:
        deploy(Path(sys.argv[1]), sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else '')
    except (PilotError, OSError, ValueError) as error:
        print(f'部署失败：{error}', file=sys.stderr)
        raise SystemExit(1)
