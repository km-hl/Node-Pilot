#!/usr/bin/env python3
"""Disposable real-systemd complete/manual recovery. Does NOT reboot the host."""
import argparse
import hashlib
import sys
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nodepilot_pkg import deploy
from nodepilot_pkg.common import PilotError, read_json, root_required, run
from nodepilot_pkg.installation import TIMER, timer_state
from nodepilot_pkg.manager import Manager
from tools.package import package
from tools.program_smoke import cleanup, prepare, require_empty


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--disposable', action='store_true', required=True)
    parser.add_argument('--legacy-source', type=Path, required=True)
    args = parser.parse_args()
    root_required(); require_empty()
    with tempfile.TemporaryDirectory(prefix='nodepilot-complete-smoke-') as tmp:
        base=Path(tmp);archive=base/'payload.tar.gz';digest=package(archive)
        payload=base/'payload';payload.mkdir()
        with tarfile.open(archive) as bundle:bundle.extractall(payload,filter='data')
        for enabled in ('enabled','enabled-runtime','disabled'):
            for active in ('active','inactive'):
                for manual in (False,True):
                    require_empty()
                    try:
                        prepare(args.legacy_source.resolve(),enabled,active)
                        original=deploy.checkpoint
                        def crash(journal,record,step):
                            original(journal,record,step)
                            if step=='checked':raise SystemExit('durable complete interruption')
                        with patch('nodepilot_pkg.deploy.checkpoint',side_effect=crash):
                            try:deploy.deploy(payload,digest,confirm_source_change=lambda old,new:True)
                            except SystemExit:pass
                            else:raise AssertionError('crash missed')
                        journal=Path('/opt/nodepilot/program-pending.json')
                        assert read_json(journal)['phase']=='complete'
                        if manual:
                            run(['systemctl','stop' if active=='active' else 'start',TIMER])
                        paths=[Path('/opt/nodepilot/.owner'),Path('/etc/nodepilot/.owner'),
                               Path('/opt/nodepilot/source.json'),Path('/usr/local/bin/nodepilot'),
                               Path('/etc/systemd/system/nodepilot-renew.service'),
                               Path('/etc/systemd/system')/TIMER,Path('/opt/nodepilot/app/current')]
                        files={p:(p.read_bytes() if not p.is_symlink() else str(p.readlink()),
                                  p.lstat().st_ino,p.lstat().st_mtime_ns) for p in paths}
                        state=timer_state();before=journal.read_bytes()
                        backups=set(Path('/etc/nodepilot/backups').iterdir())
                        command=deploy.run
                        def readonly(args,**kw):
                            assert list(args)[:2]==['systemctl','show'],args
                            return command(args,**kw)
                        m=Manager(migrate_owner=False)
                        with m.locked(migrate_owner=False),patch('nodepilot_pkg.deploy.run',side_effect=readonly), \
                             patch('nodepilot_pkg.deploy.atomic_json',side_effect=AssertionError('write')), \
                             patch('nodepilot_pkg.deploy.atomic_bytes',side_effect=AssertionError('write')):
                            if manual:
                                try:deploy.recover_program(manager=m)
                                except PilotError:pass
                                else:raise AssertionError('user state change accepted')
                                assert journal.read_bytes()==before
                            else:
                                assert deploy.recover_program(manager=m)
                                assert not deploy.recover_program(manager=m)
                        if manual:
                            run(['python3',ROOT/'nodepilot','finalize-program','--review-timer',
                                 hashlib.sha256(before).hexdigest()])
                        assert not journal.exists() and timer_state()==state
                        assert set(Path('/etc/nodepilot/backups').iterdir())==backups
                        assert {p:(p.read_bytes() if not p.is_symlink() else str(p.readlink()),
                                   p.lstat().st_ino,p.lstat().st_mtime_ns) for p in paths}==files
                        print(enabled,active,'manual timer review' if manual else 'strict complete',
                              'real systemd/files/inodes/backup invariants OK',flush=True)
                    finally:cleanup()
    print('12 complete scenarios cleaned; no real reboot claimed',flush=True)


if __name__=='__main__':main()
