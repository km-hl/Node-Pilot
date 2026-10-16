"""Source provenance is an authorization boundary, independent of archive integrity."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nodepilot_pkg.common import PilotError, atomic_json, read_json
from nodepilot_pkg.manager import OWNER, Manager
from nodepilot_pkg import project_update as update
import test_release_candidate as rc3

URL='https://github.com/example/project/releases/download/v0.4.0/nodepilot-0.4.0.tar.gz'


def release():
    return {'tag_name':'v0.4.0','assets':[{'name':name,'digest':'sha256:'+'a'*64,
        'browser_download_url':'https://github.com/example/project/releases/download/v0.4.0/'+name}
        for name in ('install.sh','nodepilot-0.4.0.tar.gz')]}


class SourceRecordTests(unittest.TestCase):
    def root(self,d):
        root=Path(d);(root/'.owner').write_text(OWNER);return root

    def test_missing_and_local_sources_block_online_discovery_without_network(self):
        for value in (None,{'schema':1,'provenance':'local-source'},
                      {'schema':1,'provenance':'local-source','previous_binding':{'repository':'example/project'}}):
            with self.subTest(value=value),tempfile.TemporaryDirectory() as d:
                root=self.root(d)
                if value:atomic_json(root/'source.json',value)
                with patch.object(update,'official_release') as network,self.assertRaisesRegex(PilotError,'未绑定在线更新源'):
                    update.check_update(root)
                network.assert_not_called()

    def test_existing_v03_rc_and_explicit_records_keep_authorized_repository(self):
        for record in ({'repository':'example/project'},
                       {'schema':1,'provenance':'official-release','repository':'example/project'},
                       {'schema':1,'provenance':'explicit-user-bound','repository':'example/project'}):
            with self.subTest(record=record),tempfile.TemporaryDirectory() as d:
                root=self.root(d);atomic_json(root/'source.json',record);before=(root/'source.json').read_bytes()
                self.assertEqual(update.bound_repository(root),'example/project')
                self.assertEqual((root/'source.json').read_bytes(),before)

    def test_abnormal_sources_never_continue_update_or_overwrite_evidence(self):
        records=[[],{}, {'repository':None},{'repository':'../project'},
                 {'schema':99,'provenance':'official-release','repository':'example/project'},
                 {'schema':True,'provenance':'official-release','repository':'example/project'},
                 {'schema':1,'provenance':[],'repository':'example/project'},
                 {'schema':1,'provenance':'local-source','repository':'example/project'}]
        for record in records:
            with self.subTest(record=record),tempfile.TemporaryDirectory() as d:
                root=self.root(d);atomic_json(root/'source.json',record);before=(root/'source.json').read_bytes()
                with patch.object(update,'official_release') as network,self.assertRaises(PilotError):update.check_update(root)
                network.assert_not_called();self.assertEqual((root/'source.json').read_bytes(),before)
        with tempfile.TemporaryDirectory() as d:
            root=self.root(d);path=root/'source.json';path.write_text('{')
            with self.assertRaises(PilotError):update.bound_repository(root)
            path.unlink();path.symlink_to(root/'.owner')
            with self.assertRaises(PilotError):update.bound_repository(root)

    def test_release_source_url_strictness_and_fork_local_never_infer_upstream(self):
        self.assertEqual(update.planned_source(None,URL)['repository'],'example/project')
        self.assertEqual(update.planned_source(None,''),{'schema':1,'provenance':'local-source'})
        for url in (URL.replace('https:','http:'),URL+'?repo=other',URL.replace('github.com','github.com.evil'),URL.replace('github.com','user@github.com')):
            with self.subTest(url=url),self.assertRaises(PilotError):update.planned_source(None,url)

    @unittest.skipUnless(os.name=='posix','global flock')
    def test_binding_requires_confirmation_valid_release_assets_and_no_transaction(self):
        with tempfile.TemporaryDirectory() as d:
            root=self.root(d);record={'schema':1,'provenance':'local-source'};atomic_json(root/'source.json',record)
            manager=Manager(root/'state',migrate_owner=False)
            manager.initialize();(manager.state/'.lock').touch()
            with patch.object(update,'official_release',return_value=release()) as network:
                with self.assertRaises(PilotError):update.bind_repository('example/project',root=root,manager=manager)
                network.assert_not_called()
                (root/'program-pending.json').write_text('{}')
                with self.assertRaises(PilotError):update.bind_repository('example/project',True,root,manager)
                network.assert_not_called();(root/'program-pending.json').unlink()
                update.bind_repository('example/project',True,root,manager)
            self.assertEqual(read_json(root/'source.json'),{'schema':1,'provenance':'explicit-user-bound','repository':'example/project'})
            self.assertEqual(update.bound_repository(root),'example/project')
            bad=release();bad['assets'][0]['digest']=''
            with patch.object(update,'official_release',return_value=bad),self.assertRaises(PilotError):update.bind_repository('example/project',True,root,manager)
            self.assertEqual(update.bound_repository(root),'example/project')


def binding_process(root, state, pipe):
    """Hold the actual flock while the parent attempts a second binding."""
    def network(repo):
        pipe.send('locked')
        if pipe.recv()!='continue':raise RuntimeError('test cancelled')
        return release()
    try:
        with patch.object(update,'official_release',side_effect=network):
            update.bind_repository('example/project',True,root,Manager(state,migrate_owner=False))
        pipe.send('bound')
    except Exception as error:
        pipe.send(type(error).__name__)
    finally:pipe.close()


@unittest.skipUnless(os.name=='posix','real global flock')
class BindingIsolationTests(unittest.TestCase):
    def environment(self,d):
        from nodepilot_pkg.manager import LEGACY_OWNER
        base=Path(d);root=base/'program';root.mkdir();(root/'.owner').write_text(LEGACY_OWNER)
        manager=Manager(base/'state',migrate_owner=False);manager.initialize()
        (manager.state/'.lock').touch()
        (manager.state/'.owner').write_text(LEGACY_OWNER)
        atomic_json(root/'source.json',{'schema':1,'provenance':'local-source','previous_binding':{'repository':'old/project'}})
        return root,manager

    def test_smoke_missing_ssh_metadata_fails_before_allocating_state(self):
        from tools import caddy_smoke, stability_smoke, adoption_smoke
        for module in (caddy_smoke,stability_smoke,adoption_smoke):
            with self.subTest(module=module.__name__),patch.dict(os.environ,{},clear=True), \
                 patch.object(module,'manager_at') as allocate,self.assertRaises(PilotError):
                module.setup()
            allocate.assert_not_called()

    def history(self,root,phase):
        return {'schema':1,'phase':phase,'root':str(root),'launcher':'/usr/local/bin/nodepilot',
            'units':'/etc/systemd/system','backup':'/not-followed/private-backup.json','nodes':[],
            'removed':[],'running':{},'enabled':{},'renew':{'nodepilot-renew.service':True,'nodepilot-renew.timer':True},
            'timer_enabled':'disabled','timer_active':False,'caddy_active':False}

    def adoption(self,phase):
        from test_config import fixture
        node=fixture();identity='a-'+'a'*16;node['adoption']=identity
        return {'schema':1,'id':identity,'phase':phase,'authorized':phase!='prepared','nodes':[node],
            'source':{'service':'fixture-xray.service','core':'xray','config':'/not-followed/config.json',
                'was_active':True,'enabled':'enabled','files':[{'path':'/not-followed/unit',
                    'target':'/not-followed/unit','sha256':'a'*64,'data':'Zml4dHVyZQ=='}]}}

    def snapshot(self,root,manager):
        return {str(p):p.read_bytes() for directory in (root,manager.state) for p in directory.rglob('*')
                if p.is_file() and not p.is_symlink()}

    def test_all_active_unknown_corrupt_and_linked_transactions_reject_before_network(self):
        cases=('program','uninstall','core','node','deletion','adoption','return','prepared','credential',
            'corrupt','unknown_schema','unknown_phase','fake_complete','adoption_corrupt','adoption_unknown',
            'symlink','linked_nodes','linked_adoptions','linked_credentials')
        for case in cases:
            with self.subTest(case=case),tempfile.TemporaryDirectory() as d:
                root,m=self.environment(d)
                path=m.state/'uninstall.json';value=self.history(root,'removing_services')
                if case=='program':path=root/'program-pending.json';value={'schema':2,'phase':'complete'}
                elif case=='core':path=m.state/'core-pending.json';value={'schema':2,'core':'xray'}
                elif case=='node':path=m.state/'nodes'/'abc123'/'pending.json';value={'schema':2,'kind':'node'}
                elif case=='deletion':path=m.state/'deletions'/'abc123.json';value={'schema':2,'node':{}}
                elif case in {'adoption','return','prepared'}:
                    phase={'adoption':'migrating','return':'restoring','prepared':'prepared'}[case]
                    value=self.adoption(phase);path=m.state/'adoptions'/value['id']/'record.json'
                elif case in {'adoption_corrupt','adoption_unknown'}:
                    value=self.adoption('active');path=m.state/'adoptions'/value['id']/'record.json'
                    if case=='adoption_corrupt':value.pop('source')
                    else:value['schema']=99
                elif case=='credential':path=m.state/'credentials'/'pending'/'profile.json';value={'schema':1}
                elif case=='unknown_schema':value=self.history(root,'complete');value['schema']=99
                elif case=='unknown_phase':value=self.history(root,'unknown')
                elif case=='fake_complete':value={'schema':1,'phase':'complete'}
                elif case.startswith('linked_'):
                    name=case.split('_',1)[1];path=m.state/name
                    if path.is_dir():path.rmdir()
                    path.symlink_to(Path(d)/'external',target_is_directory=True)
                path.parent.mkdir(parents=True,exist_ok=True)
                if case=='symlink':path.symlink_to(Path(d)/'external')
                elif not case.startswith('linked_'):
                    if case=='corrupt':path.write_text('{')
                    else:atomic_json(path,value)
                before=self.snapshot(root,m);link=path.is_symlink()
                with patch.object(update,'official_release') as network, \
                     patch.object(m,'recover',side_effect=AssertionError('destructive recovery')), \
                     patch.object(m.services,'restart',side_effect=AssertionError('service touched')):
                    with self.assertRaises(PilotError):update.bind_repository('example/project',True,root,m)
                network.assert_not_called();self.assertEqual(self.snapshot(root,m),before);self.assertEqual(path.is_symlink(),link)

    def test_terminal_history_and_backups_allow_binding_without_service_or_credential_changes(self):
        for phase in ('complete','rolled_back'):
            for adoption_phase in ('active','returned','rolled_back'):
                with self.subTest(phase=phase,adoption=adoption_phase),tempfile.TemporaryDirectory() as d:
                    root,m=self.environment(d)
                    atomic_json(m.state/'uninstall.json',self.history(root,phase))
                    adoption=self.adoption(adoption_phase);path=m.state/'adoptions'/adoption['id'];path.mkdir(parents=True)
                    atomic_json(path/'record.json',adoption)
                    (root/'program-pending-rc2.json').write_text('historical audit')
                    (m.state/'backups'/'pending.json').write_text('historical backup')
                    (m.state/'credentials').mkdir();(m.state/'credentials'/'private.ini').write_text('test secret sentinel')
                    before=self.snapshot(root,m)
                    with patch.object(update,'official_release',return_value=release()), \
                         patch.object(m.services,'restart',side_effect=AssertionError('service touched')), \
                         patch.object(m,'recover',side_effect=AssertionError('recovery invoked')):
                        update.bind_repository('example/project',True,root,m)
                    after=self.snapshot(root,m);key=str(root/'source.json');before.pop(key);after.pop(key)
                    self.assertEqual(after,before);self.assertEqual(update.bound_repository(root),'example/project')

    def test_network_metadata_atomic_write_and_source_link_failure_keep_original_bytes(self):
        for mode in ('network','digest','prerelease','write','source_link'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                root,m=self.environment(d);source=root/'source.json';original=source.read_bytes()
                record=release()
                if mode=='digest':record['assets'][1]['digest']='bad'
                elif mode=='prerelease':record['prerelease']=True
                elif mode=='source_link':source.unlink();source.symlink_to(root/'.owner')
                before=self.snapshot(root,m)
                with patch.object(update,'official_release',side_effect=PilotError('timeout') if mode=='network' else None,return_value=record) as network, \
                     patch.object(update,'atomic_json',side_effect=OSError('write failure')) if mode=='write' else patch.object(m.services,'restart',side_effect=AssertionError('restart')):
                    with self.assertRaises((PilotError,OSError)):update.bind_repository('example/project',True,root,m)
                self.assertEqual(self.snapshot(root,m),before)
                if mode=='source_link':network.assert_not_called();self.assertTrue(source.is_symlink())
                else:self.assertEqual(source.read_bytes(),original)

    def test_two_processes_share_lock_no_owner_migration_and_no_partial_binding(self):
        import multiprocessing
        from nodepilot_pkg.manager import LEGACY_OWNER
        with tempfile.TemporaryDirectory() as d:
            root,m=self.environment(d);before=(root/'source.json').read_bytes()
            context=multiprocessing.get_context('fork');parent,child=context.Pipe()
            process=context.Process(target=binding_process,args=(root,m.state,child));process.start();child.close()
            try:
                self.assertTrue(parent.poll(10));self.assertEqual(parent.recv(),'locked')
                with patch.object(update,'official_release') as network,self.assertRaises(PilotError):
                    update.bind_repository('other/project',True,root,m)
                network.assert_not_called();self.assertEqual((root/'source.json').read_bytes(),before)
                self.assertEqual((m.state/'.owner').read_text(),LEGACY_OWNER)
                parent.send('continue');self.assertTrue(parent.poll(10));self.assertEqual(parent.recv(),'bound')
                process.join(10);self.assertEqual(process.exitcode,0)
                self.assertEqual(update.bound_repository(root),'example/project')
            finally:
                if process.is_alive():process.terminate();process.join(5)
                parent.close()

    def test_external_change_during_network_is_never_overwritten(self):
        for mode in ('pending','source','source_link'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                root,m=self.environment(d);path=root/'source.json';before=path.read_bytes()
                def network(repo):
                    if mode=='pending':atomic_json(m.state/'core-pending.json',{'schema':2})
                    elif mode=='source':path.write_bytes(b'{"repository":"external/project"}\n')
                    else:path.unlink();path.symlink_to(root/'.owner')
                    return release()
                with patch.object(update,'official_release',side_effect=network),self.assertRaises(PilotError):
                    update.bind_repository('example/project',True,root,m)
                if mode=='pending':
                    self.assertEqual(path.read_bytes(),before);self.assertEqual(read_json(m.state/'core-pending.json'),{'schema':2})
                elif mode=='source':self.assertEqual(path.read_bytes(),b'{"repository":"external/project"}\n')
                else:self.assertTrue(path.is_symlink())

    def test_read_only_lock_refuses_missing_state_and_external_lock_without_writes(self):
        for mode in ('missing_state','missing_lock','symlink','hardlink'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                root,m=self.environment(d);source=(root/'source.json').read_bytes()
                external=Path(d)/'external';external.write_bytes(b'external sentinel');external.chmod(0o644)
                before=(external.read_bytes(),external.stat().st_mode,external.stat().st_mtime_ns)
                if mode=='missing_state':m.state.rename(Path(d)/'saved-state')
                else:
                    (m.state/'.lock').unlink()
                    if mode=='symlink':(m.state/'.lock').symlink_to(external)
                    elif mode=='hardlink':os.link(external,m.state/'.lock')
                with patch.object(update,'official_release') as network,self.assertRaises((PilotError,OSError)):
                    update.bind_repository('example/project',True,root,m)
                network.assert_not_called();self.assertEqual((root/'source.json').read_bytes(),source)
                self.assertEqual((external.read_bytes(),external.stat().st_mode,external.stat().st_mtime_ns),before)
                if mode=='missing_state':self.assertFalse(m.state.exists())
                elif mode=='missing_lock':self.assertFalse((m.state/'.lock').exists())

    def test_binding_write_error_after_replace_restores_exact_old_bytes_or_absence(self):
        for original in (b'{"repository": "old/project"}\n',None):
            with self.subTest(original=original),tempfile.TemporaryDirectory() as d:
                root,m=self.environment(d);source=root/'source.json'
                if original is None:source.unlink()
                else:source.write_bytes(original)
                writer=update.atomic_json
                def fail(path,record):
                    writer(path,record)
                    raise OSError('directory fsync failed after replace')
                with patch.object(update,'official_release',return_value=release()), \
                     patch.object(update,'atomic_json',side_effect=fail),self.assertRaises(OSError):
                    update.bind_repository('example/project',True,root,m)
                self.assertEqual(source.read_bytes() if source.exists() else None,original)

    def test_binding_rollback_failure_and_external_edit_never_claim_old_source_restored(self):
        for mode in ('rollback_failure','external_edit','external_link'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                root,m=self.environment(d);source=root/'source.json';writer=update.atomic_json
                def fail(path,record):
                    writer(path,record)
                    if mode=='external_edit':source.write_bytes(b'external sentinel')
                    elif mode=='external_link':source.unlink();source.symlink_to(root/'.owner')
                    raise OSError('write reported failure')
                with patch.object(update,'official_release',return_value=release()), \
                     patch.object(update,'atomic_json',side_effect=fail), \
                     patch.object(update,'atomic_bytes',side_effect=OSError('restore failed')), \
                     self.assertRaisesRegex(PilotError,'回退未确认'):
                    update.bind_repository('example/project',True,root,m)
                if mode=='external_edit':self.assertEqual(source.read_bytes(),b'external sentinel')
                elif mode=='external_link':self.assertTrue(source.is_symlink())
                else:self.assertEqual(read_json(source)['repository'],'example/project')


@unittest.skipUnless(os.name=='posix','Linux deployment provenance')
class SourceDeployTests(unittest.TestCase):
    environment=rc3.ProgramRC3Tests.environment

    def install(self,env,url='',confirm=None):
        from nodepilot_pkg.deploy import deploy
        root,launcher,units,m,source,sd=env
        with patch('nodepilot_pkg.deploy.supported'),patch('nodepilot_pkg.deploy.run',side_effect=sd):
            deploy(source,'b'*64,url,root,launcher,units,m,confirm_source_change=confirm)

    def test_first_official_and_local_install_provenance(self):
        for url,kind in ((URL,'official-release'),('','local-source')):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as d:
                env=self.environment(Path(d),first=True);self.install(env,url)
                record=read_json(env[0]/'source.json');self.assertEqual(record['provenance'],kind)
                self.assertEqual(update.active_repository(record),'example/project' if url else None)

    def test_official_to_local_requires_consent_preserves_inactive_old_binding(self):
        for legacy in (True,False):
            with self.subTest(legacy=legacy),tempfile.TemporaryDirectory() as d:
                env=self.environment(Path(d));root=env[0]
                prior={'repository':'example/project'} if legacy else {'schema':1,'provenance':'official-release','repository':'example/project'}
                atomic_json(root/'source.json',prior);before=(root/'source.json').read_bytes()
                for confirm in (None,lambda old,new:False):
                    with self.assertRaises(PilotError):self.install(env,confirm=confirm)
                    self.assertEqual((root/'source.json').read_bytes(),before)
                    self.assertEqual(list((env[3].state/'backups').iterdir()),[])
                self.install(env,confirm=lambda old,new:True)
                self.assertEqual(read_json(root/'source.json'),{'schema':1,'provenance':'local-source','previous_binding':{'repository':'example/project'}})
                with self.assertRaises(PilotError):update.bound_repository(root)

    def test_local_to_official_and_repository_switch_require_authorization(self):
        with tempfile.TemporaryDirectory() as d:
            env=self.environment(Path(d));self.install(env);root=env[0]
            with self.assertRaises(PilotError):self.install(env,URL)
            self.install(env,URL,lambda old,new:True)
            self.assertEqual(update.bound_repository(root),'example/project')
            other=URL.replace('example/project','fork/project')
            with self.assertRaises(PilotError):self.install(env,other)
            self.install(env,other,lambda old,new:True)
            self.assertEqual(update.bound_repository(root),'fork/project')
            before=list((env[3].state/'backups').iterdir());self.install(env,other)
            self.assertEqual(list((env[3].state/'backups').iterdir()),before)

    def test_local_upgrade_keeps_local_history_and_failure_restores_exact_legacy_source(self):
        from nodepilot_pkg import deploy
        with tempfile.TemporaryDirectory() as d:
            env=self.environment(Path(d));root=env[0];data=b'{"repository": "example/project"}\n'
            (root/'source.json').write_bytes(data);original=deploy.checkpoint
            def fail(journal,record,step):
                original(journal,record,step)
                if step=='timer':raise PilotError('injected upgrade failure')
            with patch('nodepilot_pkg.deploy.checkpoint',side_effect=fail),self.assertRaises(PilotError):self.install(env,confirm=lambda old,new:True)
            self.assertEqual((root/'source.json').read_bytes(),data)
            self.install(env,confirm=lambda old,new:True);before=(root/'source.json').read_bytes()
            self.install(env);self.assertEqual((root/'source.json').read_bytes(),before)

    def test_source_intents_require_v4_so_old_managers_refuse_instead_of_ignoring_policy(self):
        from nodepilot_pkg import deploy
        with tempfile.TemporaryDirectory() as d:
            env=self.environment(Path(d));original=deploy.checkpoint
            def crash(journal,record,step):
                original(journal,record,step)
                if step=='candidate':raise SystemExit('source-aware intent persisted')
            with patch('nodepilot_pkg.deploy.checkpoint',side_effect=crash),self.assertRaises(SystemExit):self.install(env)
            root,launcher,units,m,source,sd=env;journal=root/'program-pending.json';record=read_json(journal)
            self.assertEqual(record['transaction_version'],4);self.assertIn('source_after',record)
            del record['source_after'];atomic_json(journal,record);before=journal.read_bytes()
            with patch('nodepilot_pkg.deploy.run',side_effect=sd),self.assertRaises(PilotError):deploy.recover_program(root,launcher,units,m)
            self.assertEqual(journal.read_bytes(),before)
            # A damaged old v1 intent must use the same repository validator.
            record['transaction_version']=1;record['repository']='../project';atomic_json(journal,record);before=journal.read_bytes();sd.calls.clear()
            with patch('nodepilot_pkg.deploy.run',side_effect=sd),self.assertRaises(PilotError):deploy.recover_program(root,launcher,units,m)
            self.assertEqual(journal.read_bytes(),before)
            self.assertFalse(any(c[0]=='python3' for c in sd.calls))
