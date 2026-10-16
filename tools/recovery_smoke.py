#!/usr/bin/env python3
"""Mock boot/clock evidence with REAL systemd state and links; NEVER reboots."""
import argparse
import hashlib
import sys
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from nodepilot_pkg import deploy
from nodepilot_pkg.common import PilotError, atomic_json, read_json, root_required, run
from nodepilot_pkg.installation import TIMER, timer_state
from nodepilot_pkg.manager import Manager
from tools.package import package
from tools.program_smoke import cleanup, prepare, require_empty, links


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--disposable',action='store_true',required=True)
    parser.add_argument('--legacy-source',type=Path,action='append',required=True)
    parser.add_argument('--manual-only',action='store_true',help='仅验证 README 的隔离 Python/精确摘要人工恢复步骤')
    args=parser.parse_args();root_required();require_empty()
    count=0
    with tempfile.TemporaryDirectory(prefix='nodepilot-recovery-smoke-') as tmp:
        base=Path(tmp);archive=base/'payload.tar.gz';digest=package(archive)
        payload=base/'payload';payload.mkdir()
        with tarfile.open(archive) as bundle:bundle.extractall(payload,filter='data')
        for legacy in args.legacy_source:
            for enabled in ('enabled','enabled-runtime','disabled'):
                for active in ('active','inactive'):
                    for boundary in ('intent','reloaded','rollback-launcher'):
                        if args.manual_only and (enabled!='enabled-runtime' or active!='active' or boundary=='intent'):continue
                        require_empty()
                        try:
                            prepare(legacy.resolve(),enabled,active)
                            root=Path('/opt/nodepilot');old=(root/'app/current').resolve()
                            writer,command=deploy.atomic_json,deploy.run
                            def interrupt(path,record):
                                writer(path,record)
                                if path.name=='program-pending.json' and record.get('step')==boundary:
                                    raise SystemExit('durable recovery boundary')
                            def failure(argv,**kw):
                                if (boundary.startswith('rollback') and str(argv[0])=='python3' and
                                        (root/'app/current').resolve().name==digest):
                                    raise PilotError('controlled rollback')
                                return command(argv,**kw)
                            with patch('nodepilot_pkg.deploy.atomic_json',side_effect=interrupt), \
                                 patch('nodepilot_pkg.deploy.run',side_effect=failure):
                                try:deploy.deploy(payload,digest,confirm_source_change=lambda old,new:True)
                                except SystemExit:pass
                                else:raise AssertionError('interruption missed')
                            journal=root/'program-pending.json';record=read_json(journal)
                            # Explicit test preparation: emulate only the timer
                            # consequences of a reboot, outside product recovery.
                            if enabled=='enabled-runtime':run(['systemctl','disable','--runtime',TIMER])
                            observed=timer_state()
                            desired={'unit_file_state':'enabled' if enabled=='enabled' else 'disabled',
                                     'active_state':'active' if enabled=='enabled' else 'inactive'}
                            if observed['active_state']!=desired['active_state']:
                                run(['systemctl','start' if enabled=='enabled' else 'stop',TIMER])
                            assert timer_state()==desired
                            links(desired['unit_file_state'])
                            boot='ffffffff-ffff-4fff-8fff-ffffffffffff'
                            assert record['timer_context']['boot_id']!=boot
                            def recovery_command(argv,**kw):
                                argv=list(map(str,argv))
                                if argv[:2]==['systemctl','show'] and argv[4] in {
                                        'ActiveEnterTimestampMonotonic','InactiveEnterTimestampMonotonic'}:
                                    if argv[4]=='InactiveEnterTimestampMonotonic':return '0'
                                    return '20' if argv[2]=='timers.target' else '10' if enabled=='enabled' else '0'
                                assert argv[:2] not in [['systemctl',x] for x in ('start','stop','enable','disable','restart')],argv
                                return command(argv,**kw)
                            m=Manager(migrate_owner=False)
                            if args.manual_only:
                                # A historical v3 log with genuinely UNKNOWN
                                # origin boot. Do not manufacture old evidence.
                                record['transaction_version']=3;record.pop('timer_context')
                                record['previous']['boot_id']=None;atomic_json(journal,record)
                                raw=journal.read_bytes()
                                with m.locked(migrate_owner=False):
                                    try:deploy.recover_program(manager=m)
                                    except PilotError:pass
                                    else:raise AssertionError('missing boot evidence accepted')
                                assert journal.read_bytes()==raw
                                recipe="""import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).resolve(strict=True)))
from nodepilot_pkg.common import root_required
from nodepilot_pkg.manager import Manager
from nodepilot_pkg.deploy import recover_program
root_required()
manager = Manager(migrate_owner=False)
with manager.locked(migrate_owner=False, existing_only=True):
    recover_program(manager=manager, reviewed_timer=sys.argv[2])
"""
                                run(['python3','-I','-',ROOT,hashlib.sha256(raw).hexdigest()],input=recipe)
                                with m.locked(migrate_owner=False):assert not deploy.recover_program(manager=m)
                            else:
                                with m.locked(migrate_owner=False), \
                                     patch('nodepilot_pkg.installation.boot_id',return_value=boot), \
                                     patch('nodepilot_pkg.deploy.run',side_effect=recovery_command):
                                    assert deploy.recover_program(manager=m)
                                    assert not deploy.recover_program(manager=m)
                            assert timer_state()==desired and not journal.exists()
                            assert (root/'app/current').resolve()==(old if boundary=='intent' or boundary.startswith('rollback') else root/'app'/digest)
                            assert Path('/etc/nodepilot/.smoke-sentinel').read_text()=='preserve'
                            count+=1
                            print(legacy.name,enabled,active,boundary,
                                  'MANUAL digest/isolated Python; REAL state/links preserved OK' if args.manual_only else
                                  'MOCK boot/clock; REAL state/links preserved, no timer command OK',flush=True)
                        finally:cleanup()
    print(count,'mixed evidence scenarios cleaned; NO real reboot or public VPS claimed',flush=True)


if __name__=='__main__':main()
