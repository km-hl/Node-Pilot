#!/usr/bin/env python3
"""Disposable real-systemd upgrade matrix. Refuses pre-existing NodePilot paths.

Unlike unit tests this invokes actual systemctl, inspects /etc and /run links,
and uses trusted legacy installers. Requires an explicitly disposable Linux host.
"""
import argparse
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nodepilot_pkg import deploy
from nodepilot_pkg.common import PilotError, read_json, root_required, run
from nodepilot_pkg.installation import TIMER, remove_program_files, timer_state, uninstall
from nodepilot_pkg.manager import Manager, OWNER
from tools.install_smoke import tty_command
from tools.package import package

PATHS = [Path('/opt/nodepilot'), Path('/etc/nodepilot'), Path('/usr/local/bin/nodepilot'),
         Path('/etc/systemd/system/nodepilot-renew.service'), Path('/etc/systemd/system') / TIMER]


def require_empty():
    if any(p.exists() or p.is_symlink() for p in PATHS):
        raise PilotError('需要可销毁环境；拒绝覆盖已有 NodePilot 路径。')
    for name in ('nodepilot-renew.service', TIMER):
        if run(['systemctl', 'show', name, '-p', 'FragmentPath', '--value']):
            raise PilotError('同名单元已由其他位置提供。')
    for prefix in ('/etc', '/run'):
        if (Path(prefix) / 'systemd/system/timers.target.wants' / TIMER).is_symlink():
            raise PilotError('已有续期 timer 链接，拒绝测试。')


def cleanup():
    m = Manager()
    if PATHS[2].is_file():
        with m.locked(migrate_owner=False):
            deploy.recover_program(manager=m)
            uninstall(m)
        state = Path('/etc/nodepilot')
        assert not m.nodes() and state.resolve() == state
        shutil.rmtree(state)
    elif (PATHS[0] / '.owner').is_file():
        # A failed first install has no launcher but keeps a private owned
        # scaffold. Only this test created these paths, checked absent at entry.
        with m.locked(migrate_owner=False):
            deploy.recover_program(manager=m)
            assert not m.nodes() and (PATHS[0] / '.owner').read_text() == OWNER
            assert not PATHS[3].exists() and not PATHS[4].exists()
            remove_program_files(PATHS[0])
        shutil.rmtree(m.state)
    require_empty()


def links(expected):
    for prefix, state in (('/etc', 'enabled'), ('/run', 'enabled-runtime')):
        path = Path(prefix) / 'systemd/system/timers.target.wants' / TIMER
        assert path.is_symlink() == (expected == state), (prefix, expected)


def prepare(installer, enabled, active):
    tty_command(['bash', str(installer / 'install.sh'), '--local'], cwd=installer)
    run(['systemctl', 'stop', TIMER])
    run(['systemctl', 'disable', TIMER])
    run(['systemctl', 'disable', '--runtime', TIMER])
    if enabled != 'disabled':
        run(['systemctl', 'enable', *(['--runtime'] if enabled == 'enabled-runtime' else []), TIMER])
    if active == 'active':
        run(['systemctl', 'start', TIMER])
    assert timer_state() == {'unit_file_state': enabled, 'active_state': active}
    links(enabled)
    Path('/etc/nodepilot/.smoke-sentinel').write_text('preserve')


def captured():
    root = Path('/opt/nodepilot')
    paths = [root / '.owner', Path('/etc/nodepilot/.owner'), PATHS[2], PATHS[3], PATHS[4]]
    return (root / 'app/current').resolve(), {p: p.read_bytes() for p in paths}


def matrix(installer, payload, digest, label):
    for enabled in ('enabled', 'enabled-runtime', 'disabled'):
        for active in ('active', 'inactive'):
            for outcome in ('success', 'failure', 'interrupted'):
                require_empty()
                try:
                    prepare(installer, enabled, active)
                    before, files = captured()
                    m = Manager(migrate_owner=False)
                    original_run, original_checkpoint = deploy.run, deploy.checkpoint
                    def fail(args, **kwargs):
                        if (str(args[0]) == 'python3' and Path('/opt/nodepilot/app/current').resolve().name == digest):
                            raise PilotError('smoke injected final self-check failure')
                        return original_run(args, **kwargs)
                    def crash(journal, record, step):
                        original_checkpoint(journal, record, step)
                        if step == 'reloaded':
                            raise SystemExit('smoke process interruption')
                    if outcome == 'failure':
                        try:
                            with patch('nodepilot_pkg.deploy.run', side_effect=fail):
                                deploy.deploy(payload, digest, manager=m,confirm_source_change=lambda old,new:True)
                        except PilotError as error:
                            assert 'smoke injected' in str(error)
                        else:raise AssertionError('failure injection missed')
                        assert Path('/opt/nodepilot/app/current').resolve() == before
                        for path, data in files.items():assert path.read_bytes() == data, path
                    elif outcome == 'interrupted':
                        try:
                            with patch('nodepilot_pkg.deploy.checkpoint', side_effect=crash):
                                deploy.deploy(payload, digest, manager=m,confirm_source_change=lambda old,new:True)
                        except SystemExit:pass
                        else:raise AssertionError('interruption missed')
                        record = read_json('/opt/nodepilot/program-pending.json')
                        assert record['previous']['timer'] == {'unit_file_state': enabled, 'active_state': active}
                        with m.locked(migrate_owner=False):
                            assert deploy.recover_program(manager=m)
                            assert not deploy.recover_program(manager=m)
                    else:
                        deploy.deploy(payload, digest, manager=m,confirm_source_change=lambda old,new:True)
                    assert timer_state() == {'unit_file_state': enabled, 'active_state': active}
                    links(enabled)
                    assert Path('/etc/nodepilot/.smoke-sentinel').read_text() == 'preserve'
                    assert not Path('/opt/nodepilot/program-pending.json').exists()
                    print(label, enabled, active, outcome, 'real systemd + links OK', flush=True)
                finally:
                    cleanup()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--legacy-source', type=Path, action='append', default=[])
    parser.add_argument('--disposable', action='store_true', required=True)
    parser.add_argument('--first-only', action='store_true', help='仅验证首次安装成功及失败清理')
    args = parser.parse_args()
    root_required(); require_empty()
    # Track untouched external services by immutable unit/config metadata only;
    # unrelated active state changes are outside the scope of this test.
    with tempfile.TemporaryDirectory(prefix='nodepilot-program-smoke-') as tmp:
        archive = Path(tmp) / 'payload.tar.gz'
        digest = package(archive)
        import tarfile
        payload = Path(tmp) / 'payload'; payload.mkdir()
        with tarfile.open(archive) as bundle:bundle.extractall(payload, filter='data')
        try:
            tty_command(['bash', str(ROOT / 'install.sh'), '--local'])
            assert timer_state() == {'unit_file_state': 'enabled', 'active_state': 'active'}
            links('enabled')
            print('first install enabled + active real systemd OK', flush=True)
        finally:cleanup()
        original_run = deploy.run
        def fail_first(args, **kwargs):
            if (str(args[0]) == 'python3' and Path('/opt/nodepilot/app/current').is_symlink()
                    and Path('/opt/nodepilot/app/current').resolve().name == digest):
                raise PilotError('smoke first install self-check failure')
            return original_run(args, **kwargs)
        try:
            try:
                with patch('nodepilot_pkg.deploy.run', side_effect=fail_first):
                    deploy.deploy(payload, digest)
            except PilotError as error:assert 'smoke first install' in str(error)
            else:raise AssertionError('first install failure missed')
            assert not PATHS[2].exists() and not PATHS[3].exists() and not PATHS[4].exists()
            links('disabled')
            print('first install failure removes units and both link scopes real systemd OK', flush=True)
        finally:cleanup()
        for installer in ([] if args.first_only else args.legacy_source or [ROOT]):
            label = run(['python3', installer.resolve() / 'nodepilot', '--version']) + ' (' + installer.name + ')'
            matrix(installer.resolve(), payload, digest, label)
    print('all temporary installation paths and timer links cleaned', flush=True)


if __name__ == '__main__':main()
