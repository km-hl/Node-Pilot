#!/usr/bin/env python3
"""Disposable real-systemd/PTY bootstrap recovery acceptance; no production host."""
import argparse
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from nodepilot_pkg import deploy
from nodepilot_pkg.common import atomic_json, read_json, root_required
from nodepilot_pkg.installation import timer_state
from tools.install_smoke import tty_command
from tools.package import package
from tools.program_smoke import cleanup, prepare, require_empty


def blocked():
    text=tty_command(['bash',str(ROOT/'install.sh')],replies=(),expect_code=1,timeout=20)
    assert 'sudo bash install.sh --local --upgrade' in text and '请输入选项' not in text


def snapshot():
    root=Path('/opt/nodepilot')
    paths=[root/'.owner',Path('/etc/nodepilot/.owner'),Path('/usr/local/bin/nodepilot'),
           Path('/etc/systemd/system/nodepilot-renew.service'),Path('/etc/systemd/system/nodepilot-renew.timer'),
           Path('/etc/nodepilot/.smoke-sentinel')]
    return ((root/'app/current').readlink(),{p:p.read_bytes() for p in paths},timer_state(),
            set(Path('/etc/nodepilot/backups').iterdir()))


def interrupt(payload,digest,step):
    original=deploy.atomic_json
    def crash(journal,record):
        original(journal,record)
        if journal.name=='program-pending.json' and record.get('step')==step:
            raise SystemExit('bootstrap smoke interruption')
    try:
        with patch('nodepilot_pkg.deploy.atomic_json',side_effect=crash):deploy.deploy(payload,digest,confirm_source_change=lambda old,new:True)
    except SystemExit:pass
    else:raise AssertionError('interruption missed')


def anomalies():
    root=Path('/opt/nodepilot');journal=root/'program-pending.json'
    pristine=journal.read_bytes();record=read_json(journal)
    candidate=root/'app'/record['destination'];old=root/'app'/record['old']
    for mode in ('json','schema','symlink','missing_target','hash','missing_module','old_revision','owner'):
        before=snapshot();restore=lambda:None
        try:
            if mode=='json':journal.write_text('{')
            elif mode=='schema':atomic_json(journal,{**record,'schema':99})
            elif mode=='symlink':
                external=root/'test-external-evidence';external.write_bytes(pristine)
                journal.unlink();journal.symlink_to(external)
                restore=lambda:external.unlink()
            elif mode=='missing_target':
                moved=candidate.with_name('test-hidden-candidate');candidate.rename(moved)
                restore=lambda:moved.rename(candidate)
            elif mode=='hash':
                path=candidate/'nodepilot';data=path.read_bytes();path.write_text('corrupt')
                restore=lambda:path.write_bytes(data)
            elif mode=='missing_module':
                path=candidate/'nodepilot_pkg/cli.py';data=path.read_bytes();path.unlink()
                restore=lambda:path.write_bytes(data)
            elif mode=='old_revision':
                moved=old.with_name('test-original-revision');old.rename(moved);old.symlink_to(moved.name,target_is_directory=True)
                def restore():old.unlink();moved.rename(old)
            else:
                path=root/'.owner';data=path.read_bytes();path.write_text('external owner')
                restore=lambda:path.write_bytes(data)
            evidence=journal.read_bytes();target=journal.readlink() if journal.is_symlink() else None
            expected=snapshot()
            blocked()
            tty_command(['bash',str(ROOT/'install.sh'),'--local','--upgrade'],replies=(),expect_code=1,timeout=40)
            assert journal.read_bytes()==evidence
            assert not target or journal.is_symlink() and journal.readlink()==target
            assert snapshot()==expected
            print('invalid transaction:',mode,'shortcut + verified local recovery refused, evidence intact',flush=True)
        finally:
            if journal.is_symlink():journal.unlink()
            restore();journal.write_bytes(pristine)
        assert snapshot()==before


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--legacy-source',type=Path,action='append',required=True)
    parser.add_argument('--disposable',action='store_true',required=True);args=parser.parse_args()
    root_required();require_empty()
    with tempfile.TemporaryDirectory(prefix='nodepilot-bootstrap-smoke-') as tmp:
        base=Path(tmp);archive=base/'payload.tar.gz';digest=package(archive)
        import tarfile
        payload=base/'payload';payload.mkdir()
        with tarfile.open(archive) as bundle:bundle.extractall(payload,filter='data')
        # Exercise the online installer's download/verification branch without
        # pretending an unpublished RC4 GitHub attachment exists or was fetched.
        stub=base/'download-stub';stub.mkdir();curl=stub/'curl'
        curl.write_text('#!/usr/bin/env bash\nset -eu\nwhile [[ $# -gt 0 ]]; do\n'
                        'if [[ $1 == --output ]]; then cp -- '+str(archive)+' "$2"; exit 0; fi\nshift\ndone\nexit 1\n')
        curl.chmod(0o700)
        for index,legacy in enumerate(args.legacy_source):
            legacy=legacy.resolve();label=legacy.name
            for step in ('intent','current','checked'):
                require_empty()
                try:
                    prepare(legacy,'disabled','active')
                    before=snapshot()
                    tty_command(['bash',str(ROOT/'install.sh')])
                    assert snapshot()==before
                    print(label,step,'stable repeat preserves timer/files/backups OK',flush=True)
                    interrupt(payload,digest,step)
                    journal=Path('/opt/nodepilot/program-pending.json');evidence=journal.read_bytes();before=snapshot()
                    blocked();assert journal.read_bytes()==evidence and snapshot()==before
                    if legacy.name=='rc4-before-source' and step=='intent':
                        tty_command(['nodepilot'],replies=(),expect_code=1,timeout=30)
                        assert journal.read_bytes()==evidence and snapshot()==before
                        print('old RC4 manager refuses source-aware v2 intent without changing policy OK',flush=True)
                    print(label,step,'pending shortcut blocked OK',flush=True)
                    if index==0 and step=='current':anomalies()
                    if step=='checked':
                        files=[Path('/usr/local/bin/nodepilot'),Path('/opt/nodepilot/.owner'),Path('/etc/nodepilot/.owner'),
                               Path('/etc/systemd/system/nodepilot-renew.service'),Path('/etc/systemd/system/nodepilot-renew.timer'),
                               Path('/opt/nodepilot/source.json'),Path('/opt/nodepilot/app/current')]
                        stamps={p:(p.lstat().st_ino,p.lstat().st_mtime_ns) for p in files}
                        original_run=deploy.run
                        def read_only(args,**kwargs):
                            assert list(args)[:2]==['systemctl','show'], 'complete attempted a mutating command'
                            return original_run(args,**kwargs)
                        from nodepilot_pkg.manager import Manager
                        m=Manager(migrate_owner=False)
                        with m.locked(migrate_owner=False),patch('nodepilot_pkg.deploy.run',side_effect=read_only), \
                             patch('nodepilot_pkg.deploy.atomic_bytes',side_effect=AssertionError('complete file write')), \
                             patch('nodepilot_pkg.deploy.atomic_json',side_effect=AssertionError('complete journal write')):
                            assert deploy.recover_program(manager=m)
                            assert not deploy.recover_program(manager=m)
                        assert snapshot()==before
                        assert {p:(p.lstat().st_ino,p.lstat().st_mtime_ns) for p in files}==stamps
                        print(label,'complete read-only finalization with real systemd OK',flush=True)
                    backups=set(Path('/etc/nodepilot/backups').iterdir())
                    command=['bash',str(ROOT/'install.sh'),'--local','--upgrade']
                    if index==0 and step=='current':
                        command=['env','PATH='+str(stub)+':'+os.environ['PATH'],
                                 'NODEPILOT_SOURCE_URL=https://github.com/km-hl/Node-Pilot/releases/download/v0.4.0/nodepilot-0.4.0.tar.gz',
                                 'NODEPILOT_SOURCE_SHA256='+digest,'bash',str(ROOT/'install.sh'),'--upgrade']
                    tty_command(command,source_replies=('y',))
                    if command[0]=='env':
                        from nodepilot_pkg.project_update import bound_repository,read_source
                        assert read_source(Path('/opt/nodepilot'))['provenance']=='official-release'
                        assert bound_repository()=='km-hl/Node-Pilot'
                    assert not journal.exists() and Path('/opt/nodepilot/app/current').resolve().name==digest
                    assert timer_state()=={'unit_file_state':'disabled','active_state':'active'}
                    assert Path('/etc/nodepilot/.smoke-sentinel').read_text()=='preserve'
                    if step=='current' and command[0]!='env':assert set(Path('/etc/nodepilot/backups').iterdir())==backups
                    if command[0]=='env':
                        # The previous command deliberately selected Release
                        # provenance; switching back to local needs new consent.
                        tty_command(['bash',str(ROOT/'install.sh'),'--local','--upgrade'],source_replies=('y',))
                    before=snapshot();tty_command(['bash',str(ROOT/'install.sh'),'--local','--upgrade'])
                    assert snapshot()==before
                    # Historical evidence is not an active intent, even if it is
                    # not JSON. The menu must not treat it as a recovery record.
                    Path('/opt/nodepilot/program-pending-rc2.json').write_text('test history')
                    tty_command(['bash',str(ROOT/'install.sh')]);assert snapshot()==before
                    Path('/opt/nodepilot/program-pending-rc2.json').unlink()
                    print(label,step,'verified recovery + repeat + historical evidence OK',flush=True)
                finally:cleanup()
    print('bootstrap acceptance cleaned all test installations; no real ACME/DNS/public download claimed',flush=True)


if __name__=='__main__':main()
