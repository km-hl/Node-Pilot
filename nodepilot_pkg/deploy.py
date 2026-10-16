"""Verified program upgrades with durable forward recovery and retryable rollback."""
import hashlib
import os
import re
import shutil
import sys
from pathlib import Path

from . import installation
from .common import PilotError, atomic_bytes, atomic_json, fsync_dir, private_dir, read_json, run
from .installation import (LAUNCHER, RENEW_SERVICE, RENEW_TIMER, TIMER, owned_unit,
                           preflight_program, restore_timer, timer_state,
                           timer_commit, verify_timer_commit, recovery_timer_context,
                           verify_recovery_topology, timer_links)
from .manager import LEGACY_OWNER, Manager, OWNER
from .system import supported
from .project_update import (active_repository, authorize_source_change, planned_source,
                             read_source, source_label, validate_source)

UNITS = {'nodepilot-renew.service': RENEW_SERVICE, TIMER: RENEW_TIMER}
PHASES = {'preparing', 'installing', 'rolling_back', 'complete'}
REQUIRED = {'nodepilot', *('nodepilot_pkg/' + name + '.py'
                         for name in ('__init__', 'cli', 'deploy', 'manager', 'installation'))}
STEPS = {'intent','candidate','current','owners','launcher','units','reloaded',
         'timer-intent','timer','checked','legacy-migrated','rollback',
         'rollback-current','rollback-launcher','rollback-timer-cleanup',
         'rollback-units','rollback-reloaded','rollback-timer'}


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
    return {'units': previous, 'timer_exists': exists, 'timer': state, 'boot_id': installation.boot_id(),
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
    if repository is not None and repository!='':
        validate_source({'repository':repository})
    if 'source_after' in record:
        validate_source(record['source_after'])
        if (active_repository(record['source_after']) or '') != repository:
            raise PilotError('程序事务来源与仓库不一致；保留证据。')
    if 'transaction_version' not in record:
        if 'previous' in record or record['phase'] != 'installing':
            raise PilotError('程序事务缺少版本；保留证据。')
        return  # Original RC2 schema=2 intent, migrated durably before any changes.
    if type(record['transaction_version']) is not int or record['transaction_version'] not in {1,2,3,4}:
        raise PilotError('未知程序事务版本；保留证据。')
    if record['transaction_version']>=2 and 'source_after' not in record:
        raise PilotError('程序事务缺少预期来源；保留证据。')
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
    if record['transaction_version'] == 4:
        context = record.get('timer_context')
        if (not isinstance(context, dict) or set(context) != {'boot_id','timer','links','mode'} or
                not isinstance(context['mode'],str) or context['mode'] not in {'original','preserve','install'} or
                not valid_boot_id(context['boot_id']) or not valid_links(context['links']) or
                not isinstance(context['timer'], dict) or set(context['timer']) != {'unit_file_state','active_state'} or
                not isinstance(context['timer']['unit_file_state'],str) or context['timer']['unit_file_state'] not in {'enabled','enabled-runtime','disabled'} or
                not isinstance(context['timer']['active_state'],str) or context['timer']['active_state'] not in {'active','inactive'} or
                not isinstance(record.get('step'),str) or record['step'] not in STEPS or
                (context['mode']=='install' and not p['first_install']) or
                (context['mode']=='install' and record['phase']=='complete') or
                (record['phase']=='preparing' and record['step']!='intent') or
                (record['phase']=='rolling_back' and not record['step'].startswith('rollback')) or
                (record['phase']=='installing' and (record['step'].startswith('rollback') or record['step'] in {'intent','checked'}))):
            raise PilotError('程序事务 timer 恢复证据/步骤无效；保留日志。')
    if record['transaction_version'] >= 3:
        if 'boot_id' not in p or not valid_boot_id(p['boot_id']):
            raise PilotError('程序事务开始 boot_id 无效。')
        if record['phase'] == 'complete':
            c = record.get('commit')
            desired = effective_timer(record)
            if (record.get('step') != 'checked' or not isinstance(c, dict) or
                    set(c) != {'boot_id', 'timer', 'links'} or not valid_boot_id(c['boot_id']) or
                    c['timer'] != desired or not valid_links(c['links'])):
                raise PilotError('complete 提交证据缺失/无效；保留事务。')
            if record['transaction_version']==4 and any(c[k]!=record['timer_context'][k] for k in ('boot_id','timer','links')):
                raise PilotError('complete 与已确认的恢复 timer 证据不一致；保留事务。')


def effective_timer(record):
    context = record.get('timer_context') if record.get('transaction_version') == 4 else None
    if context and context['mode'] == 'preserve':
        return context['timer']
    p = record['previous']
    return {'unit_file_state':'enabled','active_state':'active'} if p['first_install'] else p['timer']


def plan_timer(record, units, reviewed=False):
    """Read-only preflight shared by forward recovery and rollback."""
    p = record['previous']
    observed = recovery_timer_context(units, run)
    verify_recovery_topology(observed,units)
    if reviewed:
        return {**observed, 'mode':'preserve'}
    context = record.get('timer_context') if record.get('transaction_version') == 4 else None
    before = context['boot_id'] if context else p.get('boot_id')
    now = observed['boot_id']
    if before is None or now is None:
        raise PilotError('非 complete 事务缺少可读取的启动周期证据；保留日志。按 README 人工核对 timer 后以日志 SHA-256 恢复，不能删除日志。')
    if before == now:
        if context is None:
            return {**observed, 'mode':'install' if p['first_install'] else 'original'}
        if context['mode']=='preserve':
            if observed != {k:context[k] for k in ('boot_id','timer','links')}:
                raise PilotError('本次启动中 timer 已偏离恢复时确认的策略；保留日志，不覆盖用户操作。')
        elif observed['links'] != context['links'] and record['step'] not in {'timer-intent','rollback','rollback-launcher'}:
            raise PilotError('timer 启用链接在事务操作范围外变化；保留日志。')
        return context
    if context is None:
        raise PilotError('旧事务没有升级前的启用链接证据，不能自动跨重启恢复；保留日志并按 README 人工核对。')
    if not (units/TIMER).exists() and not p['timer_exists'] and not any(context['links'].values()) and context['timer']==observed['timer']:
        pass  # Nothing was installed/enabled: no old runtime policy to replay.
    else:
        verify_timer_commit({k:context[k] for k in ('boot_id','timer','links')}, context['timer'], units, run)
    return {**observed, 'mode':'install' if context['mode']=='install' else 'preserve'}


def reconcile_timer(record, units):
    context = record['timer_context']
    if context['mode']=='preserve':
        if recovery_timer_context(units,run) != {k:context[k] for k in ('boot_id','timer','links')}:
            raise PilotError('恢复期间 timer 策略/状态/链接变化；未重放旧快照。')
    else:
        restore_timer(effective_timer(record),run)


def save_timer(record, units, journal, step):
    mode = record['timer_context']['mode']
    record['timer_context'] = {**recovery_timer_context(units,run),
                               'mode':'original' if mode=='install' else mode}
    checkpoint(journal,record,step)


def recovery_files(record, root, launcher, units, manager):
    """Reject unknown contents before replaying any owned file write."""
    p = record['previous']
    for path, allowed in [(launcher,{p['launcher'],LAUNCHER}),
                          (root/'.owner',{p['root_owner'],OWNER})]:
        if text_or_none(path) not in allowed:
            raise PilotError(f'恢复文件被外部修改：{path.name}；保留事务。')
    if manager is not None and text_or_none(manager.state/'.owner') not in {p['state_owner'],OWNER}:
        raise PilotError('恢复状态 owner 已被外部修改；保留事务。')
    for name in UNITS:
        if text_or_none(units/name) not in {p['units'][name],UNITS[name]}:
            raise PilotError('恢复单元内容与原/目标快照不一致；保留事务。')
    source = read_source(root)
    if text_or_none(root/'source.json') != p['source'] and source != expected_source(record):
        raise PilotError('恢复来源被外部修改；保留事务。')
    current = root/'app/current'
    actual = current.resolve().name if current.is_symlink() else None
    if record['phase']=='preparing':
        if (actual != record['old'] or text_or_none(launcher)!=p['launcher'] or
                any(text_or_none(units/n)!=p['units'][n] for n in UNITS) or
                (p['root_owner'] is not None and text_or_none(root/'.owner')!=p['root_owner']) or
                (manager is not None and text_or_none(manager.state/'.owner')!=p['state_owner'])):
            raise PilotError('preparing 阶段与实际文件不一致；未执行候选，保留事务。')
    elif record.get('transaction_version')==4 and record['phase']=='installing' and record['step'] not in {'candidate','legacy-migrated'}:
        if actual != record['destination']:
            raise PilotError('已切换阶段的 current 不指向目标；保留事务。')
        order=['current','owners','launcher','units','reloaded','timer-intent','timer']
        index=order.index(record['step'])
        if index>=1 and (text_or_none(root/'.owner')!=OWNER or
                (manager is not None and text_or_none(manager.state/'.owner')!=OWNER)):
            raise PilotError('owner 阶段与实际文件不一致；保留事务。')
        if index>=2 and text_or_none(launcher)!=LAUNCHER:
            raise PilotError('launcher 阶段与实际文件不一致；保留事务。')
        if index>=3 and any(text_or_none(units/n)!=UNITS[n] for n in UNITS):
            raise PilotError('units 阶段与实际文件不一致；保留事务。')
    elif record.get('transaction_version')==4 and record['phase']=='rolling_back':
        steps=['rollback','rollback-current','rollback-launcher','rollback-timer-cleanup',
               'rollback-units','rollback-reloaded','rollback-timer']
        index=steps.index(record['step'])
        if index>=1 and actual!=record['old']:
            raise PilotError('回滚 current 阶段与实际不一致；保留事务。')
        if index>=2 and text_or_none(launcher)!=p['launcher']:
            raise PilotError('回滚 launcher 阶段与实际不一致；保留事务。')
        if index>=4 and any(text_or_none(units/n)!=p['units'][n] for n in UNITS):
            raise PilotError('回滚 units 阶段与实际不一致；保留事务。')


def persist_timer_plan(record, plan, journal):
    migrated = record.get('transaction_version') != 4
    changed = record.get('timer_context') != plan
    if migrated or changed:
        # Preserve exact historical intent before migrating recovery semantics.
        raw = journal.read_bytes()
        audit = journal.parent / ('program-pending-v'+str(record['transaction_version'])+'-'+hashlib.sha256(raw).hexdigest()[:16]+'.json')
        if audit.is_symlink() or (audit.exists() and (not audit.is_file() or audit.stat().st_size!=len(raw) or audit.read_bytes()!=raw)):
            raise PilotError('历史事务审计证据冲突；保留活动日志。')
        if not audit.exists():atomic_bytes(audit,raw)
    if migrated:
        record['transaction_version']=4
        record['previous'].setdefault('boot_id',None)
        if record.get('step') not in STEPS:
            record['step']='legacy-migrated' if record['phase']=='installing' else 'rollback' if record['phase']=='rolling_back' else 'intent'
        if 'source_after' not in record:
            record['source_after']=expected_source(record) or read_source(journal.parent) or {'schema':1,'provenance':'local-source'}
            record['repository']=active_repository(record['source_after']) or ''
    if migrated or changed:
        record['timer_context']=plan
        atomic_json(journal,record)


def valid_boot_id(value):
    return value is None or (isinstance(value, str) and re.fullmatch(
        r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value) is not None)


def valid_links(value):
    if not isinstance(value, dict) or set(value) != {'persistent', 'runtime'}:
        return False
    for links in value.values():
        if not isinstance(links, dict) or len(links) > 128:
            return False
        for name, info in links.items():
            if (not isinstance(name, str) or name.startswith('/') or '..' in name.split('/') or
                    not isinstance(info, dict) or set(info) != {'target','device','inode','mtime_ns','ctime_ns'} or
                    not isinstance(info['target'], str) or any(type(info[k]) is not int or info[k] < 0
                    for k in ('device','inode','mtime_ns','ctime_ns'))):
                return False
    return True


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
    current = app/'current'
    if (name is None and not current.exists() and not current.is_symlink()) or (
            name is not None and current.is_symlink() and current.readlink()==Path(name)):
        return
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
    if text_or_none(path)==content:
        return
    if content is None:
        path.unlink(missing_ok=True)
        fsync_dir(path.parent)
    else:
        atomic_bytes(path, content.encode(), mode=mode)


def checkpoint(journal, record, step):
    record['step'] = step
    atomic_json(journal, record)


def expected_source(record):
    if 'source_after' in record:return record['source_after']
    if record.get('repository'):return {'repository':record['repository']}
    return None


def finalize_program(record, root, launcher, units, manager, reviewed_timer=False):
    """Read-only validation of a committed installation; unlink the intent LAST."""
    app=root/'app';destination=app/record['destination'];current=app/'current'
    if not current.is_symlink() or current.resolve()!=destination.resolve():
        raise PilotError('complete 校验失败：current 不指向事务目标；保留日志。')
    verify_candidate(destination,record['hashes'],record.get('target_version'))
    if text_or_none(root/'.owner')!=OWNER or (manager is not None and text_or_none(manager.state/'.owner')!=OWNER):
        raise PilotError('complete 校验失败：owner schema 不匹配；保留日志。')
    if text_or_none(launcher)!=LAUNCHER:
        raise PilotError('complete 校验失败：launcher 不匹配；保留日志。')
    for name,content in UNITS.items():
        if text_or_none(units/name)!=content:
            raise PilotError(f'complete 校验失败：{name} 内容不匹配；保留日志。')
    p=record['previous']
    desired=effective_timer(record)
    try:
        verify_timer_commit(record.get('commit') if record.get('transaction_version') in {3,4} else None,
                            desired, units, run, reviewed=reviewed_timer)
    except (PilotError, OSError) as error:
        raise PilotError(f'complete timer 校验失败：{error} 保留日志；按 README 人工核对后使用 '
                         'finalize-program --review-timer <日志SHA256>，不要删除活动日志。') from error
    source=expected_source(record)
    if source is not None:
        if read_source(root)!=source:
            raise PilotError('complete 校验失败：source.json 不匹配；保留日志。')
    else:
        read_source(root)
        if text_or_none(root/'source.json')!=p['source']:
            raise PilotError('complete 校验失败：原 source.json 已变化；保留日志。')
    (root/'program-pending.json').unlink()
    fsync_dir(root)


def review_complete(manager, digest, root=Path('/opt/nodepilot'),
                    launcher=Path('/usr/local/bin/nodepilot'), units=Path('/etc/systemd/system')):
    """Explicit acknowledgement of timer evidence only, tied to exact private intent."""
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise PilotError('人工最终化需要已核对日志的 SHA-256。')
    root, launcher, units = map(Path, (root, launcher, units))
    with manager.locked(migrate_owner=False, existing_only=True):
        assert_program_owned(root, launcher, units)
        journal = root / 'program-pending.json'
        if journal.is_symlink() or not journal.is_file() or journal.stat().st_size > 256 * 1024:
            raise PilotError('程序活动日志缺失或不是有界普通文件。')
        if hashlib.sha256(journal.read_bytes()).hexdigest() != digest:
            raise PilotError('日志 SHA-256 已变化，必须重新核对；未最终化。')
        record = read_json(journal)
        validate_record(record, root, launcher, units, manager)
        if record['phase'] != 'complete':
            raise PilotError('仅允许人工核对 complete 的 timer，未完成升级不能跳过。')
        finalize_program(record, root, launcher, units, manager, reviewed_timer=True)


def rollback_program(record, root, launcher, units, manager, timer_plan=None):
    """Journal remains rolling_back until every restore, including systemd, succeeds."""
    journal = root / 'program-pending.json'
    if record['phase']!='rolling_back':
        record['phase'] = 'rolling_back'
        checkpoint(journal, record, 'rollback')
    recovery_files(record,root,launcher,units,manager)
    plan=timer_plan if timer_plan is not None else plan_timer(record,units)
    if plan['mode']=='preserve' and recovery_timer_context(units,run)!={k:plan[k] for k in ('boot_id','timer','links')}:
        raise PilotError('回滚前 timer 偏离已确认的本次启动策略；保留 rolling_back。')
    persist_timer_plan(record,plan,journal)
    p = record['previous']
    if record['old'] is not None and (root / 'app' / record['old']).is_symlink():
        raise PilotError('原程序修订为外部链接；保留事务。')
    if record['old'] is not None and not (root / 'app' / record['old']).is_dir():
        raise PilotError('原程序修订缺失；保留事务。')
    point(root / 'app', record['old'])
    checkpoint(journal,record,'rollback-current')
    restore_file(launcher, p['launcher'], 0o755)
    checkpoint(journal,record,'rollback-launcher')
    # Keep the unit available until its symlinks and running state are removed.
    if not p['timer_exists'] and (units / TIMER).exists():
        cleanup=recovery_timer_context(units,run)
        verify_recovery_topology(cleanup,units)
        if cleanup['timer']['active_state']=='active':run(['systemctl','stop',TIMER])
        if cleanup['links']['persistent']:run(['systemctl','disable',TIMER])
        if cleanup['links']['runtime']:run(['systemctl','disable','--runtime',TIMER])
        record['timer_context']={**recovery_timer_context(units,run),'mode':'preserve'}
        checkpoint(journal,record,'rollback-timer-cleanup')
    for name, content in p['units'].items():
        restore_file(units / name, content, 0o644)
    checkpoint(journal,record,'rollback-units')
    run(['systemctl', 'daemon-reload'])
    checkpoint(journal,record,'rollback-reloaded')
    if p['timer_exists']:
        reconcile_timer(record,units)
    save_timer(record,units,journal,'rollback-timer')
    restore_file(root / 'source.json', p['source'])
    if manager is not None:
        restore_file(manager.state / '.owner', p['state_owner'])
    # First-install failure retains an owned empty scaffold for safe retry.
    if p['root_owner'] is not None:
        restore_file(root / '.owner', p['root_owner'])
    journal.unlink()
    fsync_dir(root)


def recover_program(root=Path('/opt/nodepilot'), launcher=Path('/usr/local/bin/nodepilot'), units=Path('/etc/systemd/system'), manager=None, reviewed_timer=None):
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
    if reviewed_timer is not None:
        if (not isinstance(reviewed_timer,str) or not re.fullmatch('[0-9a-f]{64}',reviewed_timer) or
                hashlib.sha256(journal.read_bytes()).hexdigest()!=reviewed_timer):
            raise PilotError('人工恢复需要已核对的精确日志 SHA-256；未改变事务。')
        if record['phase']=='complete':
            raise PilotError('complete 请使用原有 finalize-program 人工入口。')
    app = root / 'app'
    if record['phase']=='complete':
        finalize_program(record,root,launcher,units,manager)
        return True
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
        # This is a current observation, NOT proof of the RC2 transaction boot.
        record['previous']['boot_id']=None
        record['transaction_version'] = 1
        checkpoint(journal, record, 'legacy-migrated')
        reviewed_timer=True  # RC2 has no old timer snapshot to replay at all.
    recovery_files(record,root,launcher,units,manager)
    if record['phase']=='installing':
        verify_candidate(destination, record['hashes'], record.get('target_version'))
    plan=plan_timer(record,units,reviewed=reviewed_timer is not None)
    persist_timer_plan(record,plan,journal)
    if record['phase'] == 'rolling_back':
        rollback_program(record, root, launcher, units, manager, timer_plan=plan)
        return True
    if record['phase'] == 'preparing':
        # Interrupted copies are never executed; original installation is intact.
        rollback_program(record, root, launcher, units, manager, timer_plan=plan)
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
        desired = effective_timer(record)
        checkpoint(journal,record,'timer-intent')
        reconcile_timer(record,units)
        save_timer(record,units,journal,'timer')
        run(['python3', destination / 'nodepilot', 'self-check'])
        source=expected_source(record)
        if source is not None:
            atomic_json(root / 'source.json', source)
        if record['transaction_version'] >= 3:
            record['commit'] = timer_commit(units, run)
            if record['commit']['timer'] != desired:
                raise PilotError('提交前 timer 状态变化；拒绝记录 complete。')
            if record['transaction_version']==4 and any(record['commit'][k]!=record['timer_context'][k] for k in ('boot_id','timer','links')):
                raise PilotError('提交前 timer 证据变化；拒绝记录 complete。')
        record['phase'] = 'complete'
        checkpoint(journal, record, 'checked')
    except (Exception, KeyboardInterrupt) as error:
        if record['phase'] == 'complete':
            # The complete write may already be durable even if fsync/reporting
            # failed. Never undo a committed transaction on this error path.
            raise PilotError('complete 提交写入返回错误；保留事务，下次只读核对最终化。') from error
        try:
            rollback_program(record, root, launcher, units, manager, timer_plan=record['timer_context'])
        except (Exception, KeyboardInterrupt) as rollback:
            raise PilotError(f'程序升级失败，回滚未完成：{rollback}；保留 program-pending.json，请重试恢复。') from error
        raise
    journal.unlink()
    fsync_dir(root)
    return True


def deploy(source, digest, source_url='', root=Path('/opt/nodepilot'), launcher=Path('/usr/local/bin/nodepilot'),
           units=Path('/etc/systemd/system'), manager=None, confirm_source_change=None):
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
        prior_source=read_source(root)
        next_source=planned_source(prior_source,source_url)
        authorize_source_change(prior_source,next_source,confirm_source_change,installed=old is not None)
        # Recovery may already have installed this exact verified payload. Do
        # not create another transaction/backup or reload/restart units on retry.
        if old and old.name == digest.lower():
            verify_candidate(old, hashes)
            if (text_or_none(launcher) == LAUNCHER and text_or_none(root / '.owner') == OWNER
                    and text_or_none(manager.state / '.owner') == OWNER
                    and all(text_or_none(units / name) == content for name, content in UNITS.items())
                    and prior_source==next_source):
                return
        previous = snapshot(root, launcher, units, manager, old.name if old else None)
        context = {'boot_id':previous['boot_id'], 'timer':dict(previous['timer']),
                   'links':timer_links(units), 'mode':'install' if previous['first_install'] else 'original'}
        backup = manager.backup()
        private_dir(root)
        if not (root / '.owner').exists():
            atomic_bytes(root / '.owner', OWNER.encode())
        private_dir(root / 'cores')
        app = private_dir(root / 'app')
        destination = app / digest.lower()
        # Older RC3/RC4 managers must reject reboot-aware intents explicitly.
        record = {'schema': 2, 'transaction_version': 4, 'phase': 'preparing', 'step': 'intent',
                  'root': str(root), 'launcher': str(launcher), 'units': str(units),
                  'destination': destination.name, 'old': old.name if old else None,
                  'old_version': version(old) if old else None, 'target_version': version(source),
                  'backup': str(backup), 'previous': previous,
                  'repository': active_repository(next_source) or '', 'source_after':next_source,
                  'hashes': hashes, 'timer_context':context}
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


def confirm_source(previous,target):
    with open('/dev/tty','r',encoding='utf-8') as reader, open('/dev/tty','w',encoding='utf-8') as writer:
        note='旧绑定将保留为非活动历史。' if target.get('provenance')=='local-source' and active_repository(previous) else '仅在确认后变更在线更新来源。'
        print(f'安装来源：{source_label(previous)} → {source_label(target)}。{note}',file=writer)
        print('确认变更安装来源（y/N）：',end='',file=writer,flush=True)
        return reader.readline().strip().lower()=='y'


if __name__ == '__main__':
    try:
        deploy(Path(sys.argv[1]), sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else '',confirm_source_change=confirm_source)
    except (PilotError, OSError, ValueError) as error:
        print(f'部署失败：{error}', file=sys.stderr)
        raise SystemExit(1)
