"""RC4 bootstrap regression. Remapped installer tests never change system paths."""
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nodepilot_pkg import deploy
from nodepilot_pkg.common import PilotError, atomic_json, read_json
from nodepilot_pkg.installation import LAUNCHER
from nodepilot_pkg.manager import LEGACY_OWNER
import test_release_candidate as rc3

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Linux/bash bootstrap')
class InstallerShortcutTests(unittest.TestCase):
    def environment(self, base):
        root=base/'program';root.mkdir();launcher=base/'bin/nodepilot';launcher.parent.mkdir()
        os_release=base/'os-release';os_release.write_text('ID=debian\nVERSION_ID=13\n')
        systemd=base/'systemd';systemd.mkdir();tty=base/'tty';tty.touch()
        text=(ROOT/'install.sh').read_text()
        for old,new in (('/opt/nodepilot',str(root)),('/usr/local/bin/nodepilot',str(launcher)),
                        ('/etc/os-release',str(os_release)),('/run/systemd/system',str(systemd)),('/dev/tty',str(tty))):
            text=text.replace(old,new)
        # Permit unprivileged CI; all installer filesystem references above are
        # private, and these tests only exercise the pre-dependency shortcut.
        text=text.replace('[[ ${EUID} -eq 0 ]]','true')
        script=base/'install.sh';script.write_text(text)
        launcher.write_text(LAUNCHER.replace('/opt/nodepilot',str(root)));launcher.chmod(0o700)
        (root/'.owner').write_text(LEGACY_OWNER)
        app=root/'app/old';app.mkdir(parents=True)
        marker=base/'old-menu-ran'
        (app/'nodepilot').write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("menu")\n')
        (root/'app/current').symlink_to('old',target_is_directory=True)
        return root,launcher,script,marker

    def test_stable_repeat_and_historical_evidence_enter_existing_menu(self):
        for history in (False,True):
            with self.subTest(history=history),tempfile.TemporaryDirectory() as d:
                root,launcher,script,marker=self.environment(Path(d))
                if history:(root/'program-pending-rc2.json').write_text('historical evidence')
                before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
                result=subprocess.run(['bash',str(script)],capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr);self.assertTrue(marker.exists())
                for path,data in before.items():self.assertEqual(path.read_bytes(),data)

    def test_all_activity_or_anomalies_refuse_shortcut_without_parsing_or_execution(self):
        for mode in ('valid','json','schema','directory','symlink','dangling','missing_launcher','owner'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                base=Path(d);root,launcher,script,marker=self.environment(base)
                outside=base/'outside';outside.write_text('external evidence')
                pending=root/'program-pending.json'
                if mode=='directory':pending.mkdir()
                elif mode in ('symlink','dangling'):pending.symlink_to(outside if mode=='symlink' else base/'absent')
                else:pending.write_text('{' if mode=='json' else json.dumps({'schema':99 if mode=='schema' else 2,'phase':'preparing'}))
                if mode=='missing_launcher':launcher.unlink()
                if mode=='owner':(root/'.owner').write_text('external owner')
                before=pending.lstat();data=pending.read_bytes() if pending.is_file() else None
                result=subprocess.run(['bash',str(script)],capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,1,result.stderr);self.assertFalse(marker.exists())
                self.assertIn('sudo bash install.sh --local --upgrade',result.stderr)
                after=pending.lstat()
                self.assertEqual((after.st_ino,after.st_size,after.st_mtime_ns),(before.st_ino,before.st_size,before.st_mtime_ns))
                if data is not None:self.assertEqual(pending.read_bytes(),data)
                self.assertEqual(outside.read_text(),'external evidence')


@unittest.skipUnless(os.name=='posix','Linux durable transactions')
class ProgramRC4Tests(unittest.TestCase):
    environment=rc3.ProgramRC3Tests.environment
    install=rc3.ProgramRC3Tests.install
    recover=rc3.ProgramRC3Tests.recover

    def pending(self, env, step='current'):
        original=deploy.checkpoint
        def crash(journal,record,current):
            original(journal,record,current)
            if current==step:raise SystemExit('RC4 interruption')
        with patch('nodepilot_pkg.deploy.checkpoint',side_effect=crash),self.assertRaises(SystemExit):self.install(env)

    def test_recovery_and_same_payload_retry_do_not_backup_reload_or_change_timer(self):
        for enabled,active in rc3.ProgramRC3Tests.states:
            with self.subTest(enabled=enabled,active=active),tempfile.TemporaryDirectory() as d:
                env=self.environment(Path(d),enabled,active);root,launcher,units,m,source,sd=env
                self.pending(env)
                backups=set((m.state/'backups').iterdir())
                self.install(env) # recover, then detect the requested payload is already installed
                self.assertFalse((root/'program-pending.json').exists())
                self.assertEqual(set((m.state/'backups').iterdir()),backups)
                sd.calls.clear();self.install(env);self.install(env)
                self.assertEqual(set((m.state/'backups').iterdir()),backups)
                changes=[c for c in sd.calls if c[0]=='systemctl' and c[1]!='show']
                self.assertEqual(changes,[])
                self.assertEqual(sd.state(),{'unit_file_state':enabled,'active_state':active})
                self.assertEqual((m.state/'user-config').read_text(),'preserved')

    def test_missing_required_module_and_abnormal_old_revision_preserve_journal(self):
        for mode in ('module','old_missing','old_symlink','old_entry_symlink','old_package_symlink','target_version','old_version'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                env=self.environment(Path(d));root,launcher,units,m,source,sd=env
                self.pending(env);journal=root/'program-pending.json';before=journal.read_bytes()
                old=root/'app'/('a'*64)
                if mode=='module':
                    new=root/'app'/('b'*64);entry='nodepilot_pkg/cli.py';(new/entry).unlink()
                    # A coherent but incomplete manifest must also be refused.
                    manifest=read_json(new/'files.json');manifest['files'].remove(entry);del manifest['hashes'][entry]
                    atomic_json(new/'files.json',manifest)
                elif mode=='old_missing':old.rename(old.with_name('missing'))
                elif mode=='old_symlink':
                    old.rename(old.with_name('original'));old.symlink_to('original',target_is_directory=True)
                elif mode=='old_entry_symlink':
                    (old/'nodepilot').unlink();(old/'nodepilot').symlink_to(source/'nodepilot')
                elif mode=='old_package_symlink':(old/'nodepilot_pkg').symlink_to(source/'nodepilot_pkg',target_is_directory=True)
                else:
                    record=read_json(journal);record[mode]='99.0.0';atomic_json(journal,record);before=journal.read_bytes()
                sd.calls.clear()
                with self.assertRaises(PilotError):self.recover(env)
                self.assertEqual(journal.read_bytes(),before)
                self.assertFalse(any(c[0]=='python3' for c in sd.calls))
                self.assertEqual((root/'app/current').resolve().name,'b'*64)

    def test_menu_and_renew_reject_invalid_transaction_before_owner_migration(self):
        from nodepilot_pkg.cli import main
        from nodepilot_pkg.menu import Menu
        for command in ('menu','renew'):
            with self.subTest(command=command),tempfile.TemporaryDirectory() as d:
                env=self.environment(Path(d));root,launcher,units,m,source,sd=env
                atomic_json(root/'program-pending.json',{'schema':99})
                original=deploy.recover_program
                def recover(**kwargs):return original(root,launcher,units,m)
                with patch('nodepilot_pkg.deploy.recover_program',side_effect=recover),patch('nodepilot_pkg.deploy.run',side_effect=sd):
                    if command=='menu':
                        menu=object.__new__(Menu);menu.manager=m
                        with self.assertRaises(PilotError):menu.start()
                    else:
                        with patch('nodepilot_pkg.manager.Manager',return_value=m),patch('nodepilot_pkg.system.supported'),patch('sys.stderr',new=io.StringIO()):
                            self.assertEqual(main(['renew']),1)
                self.assertEqual((m.state/'.owner').read_text(),LEGACY_OWNER)
                self.assertTrue((root/'program-pending.json').exists())

    def test_special_or_oversized_journal_refuses_without_read_or_execution(self):
        for mode in ('fifo','directory','oversized'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                env=self.environment(Path(d));journal=env[0]/'program-pending.json'
                if mode=='fifo':os.mkfifo(journal)
                elif mode=='directory':journal.mkdir()
                else:journal.write_bytes(b' '*(256*1024+1))
                with self.assertRaises(PilotError):self.recover(env)
                self.assertTrue(journal.exists())
                self.assertFalse(any(c[0]=='python3' for c in env[-1].calls))
